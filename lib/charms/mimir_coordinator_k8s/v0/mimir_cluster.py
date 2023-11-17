"""This file defines the schemas for the provider and requirer sides of this relation interface.

It must expose two interfaces.schema_base.DataBagSchema subclasses called:
- ProviderSchema
- RequirerSchema

TODO: see https://github.com/canonical/charm-relation-interfaces/issues/121
"""
import json
import logging
import socket
from enum import Enum
from typing import Any, Dict, MutableMapping, Set
from typing import Optional
from urllib.parse import urlparse

import ops
import pydantic
from ops import Object
from pydantic import BaseModel

log = logging.getLogger("mimir_cluster")
LIBID = "9818a8d44028454a94c6c3a01f4316d2"
DEFAULT_ENDPOINT_NAME = "mimir-cluster"

LIBAPI = 0
LIBPATCH = 1

BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}


class MimirClusterError(Exception):
    """Base class for exceptions raised by this module."""


class DataValidationError(MimirClusterError):
    """Raised when relation databag validation fails."""


class DatabagAccessPermissionError(MimirClusterError):
    """Raised when a follower attempts to write leader settings."""


class DatabagModel(BaseModel):
    """Base databag model."""

    class Config:
        """Pydantic config."""

        allow_population_by_field_name = True
        """Allow instantiating this class by field name (instead of forcing alias)."""

    _NEST_UNDER = None

    @classmethod
    def load(cls, databag: MutableMapping):
        """Load this model from a Juju databag."""
        if cls._NEST_UNDER:
            return cls.parse_obj(json.loads(databag[cls._NEST_UNDER]))

        try:
            data = {k: json.loads(v) for k, v in databag.items() if k not in BUILTIN_JUJU_KEYS}
        except json.JSONDecodeError as e:
            msg = f"invalid databag contents: expecting json. {databag}"
            log.error(msg)
            raise DataValidationError(msg) from e

        try:
            return cls.parse_raw(json.dumps(data))  # type: ignore
        except pydantic.ValidationError as e:
            msg = f"failed to validate databag: {databag}"
            log.error(msg, exc_info=True)
            raise DataValidationError(msg) from e

    def dump(self, databag: Optional[MutableMapping] = None, clear: bool = True):
        """Write the contents of this model to Juju databag.

        :param databag: the databag to write the data to.
        :param clear: ensure the databag is cleared before writing it.
        """
        if clear and databag:
            databag.clear()

        if databag is None:
            databag = {}

        if self._NEST_UNDER:
            databag[self._NEST_UNDER] = self.json()

        dct = self.dict()
        for key, field in self.__fields__.items():  # type: ignore
            value = dct[key]
            databag[field.alias or key] = json.dumps(value)

        return databag


class DataBagSchema(BaseModel):
    """Base class for relation interface databag schemas.

    Subclass from this base class and override "unit" and/or "app" to create a specification for
    a databag schema.

    For example:

    >>> from pydantic import Json
    >>>
    >>> class MyUnitConsumerSchema(DataBagSchema):
    >>>     foo: Json[int]
    >>>     bar: str
    >>>
    >>> # this class needs to be named "ConsumerSchema"
    >>> # for it to be picked up by the automated tester.
    >>> class ConsumerSchema(DataBagSchema):
    >>>     unit: MyUnitConsumerSchema

    This specifies that for a relation to satisfy MyRequirerSchema, the application databag
    needs to be empty and the unit databag needs to contain exactly a "bar":string and a
    "foo":Json-encoded int value.

    By using pydantic's validator API, you can specify further constraints on the values,
    provide defaults, enforce encoding/decoding, and more.
    """
    unit: Optional[DatabagModel] = None
    app: Optional[DatabagModel] = None


class MimirRole(str, Enum):
    """Mimir component role names."""
    overrides_exporter = "overrides_exporter"
    query_scheduler = "query_scheduler"
    flusher = "flusher"
    query_frontend = "query_frontend"
    querier = "querier"
    store_gateway = "store_gateway"
    ingester = "ingester"
    distributor = "distributor"
    ruler = "ruler"
    alertmanager = "alertmanager"
    compactor = "compactor"


class MimirClusterProviderAppData(DatabagModel):
    mimir_config: Dict[str, Any]


class ProviderSchema(DataBagSchema):
    """The schema for the provider side of this interface."""
    app: MimirClusterProviderAppData


class JujuTopology(pydantic.BaseModel):
    model: str
    unit: str
    # ...


class MimirClusterRequirerUnitData(DatabagModel):
    juju_topology: JujuTopology
    address: str


class MimirClusterRequirerAppData(DatabagModel):
    roles: Dict[MimirRole, int]


class RequirerSchema(DataBagSchema):
    """The schema for the requirer side of this interface."""
    unit: MimirClusterRequirerUnitData
    app: MimirClusterRequirerAppData


class MimirClusterProvider(Object):
    def __init__(self, charm, key: Optional[str] = None,
                 endpoint: str = DEFAULT_ENDPOINT_NAME):
        super().__init__(charm, key)
        self._charm = charm
        self._relations = self.model.relations[endpoint]

    def publish_configs(self,
                        mimir_config: Dict[str, Any],
                        ) -> None:
        """Publish the mimir config to all related mimir worker clusters."""
        databag_model = MimirClusterProviderAppData(
            mimir_config=mimir_config,
        )
        for relation in self._relations:
            if relation:
                local_app_databag = relation.data[self.model.app]
                databag_model.dump(local_app_databag)

    def gather_roles(self) -> Dict[MimirRole, int]:
        """Go through the worker's app databags and sum the available application roles."""
        data = {}
        for relation in self._relations:
            if relation.app:
                remote_app_databag = relation.data[relation.app]
                worker_roles: Dict[MimirRole, int] = MimirClusterRequirerAppData.load(remote_app_databag).roles
                for role, role_n in worker_roles.items():
                    if role not in data:
                        data[role] = 0
                    data[role] += role_n
        return data

    def gather_addresses(self) -> Set[str]:
        """Go through the worker's unit databags to collect all the addresses published by the units."""
        data = set()
        for relation in self._relations:
            for worker_unit in relation.units:
                worker_data = MimirClusterRequirerUnitData.load(relation.data[worker_unit])
                unit_address = worker_data.address
                data.add(unit_address)

        return data


class MimirClusterRequirer(Object):
    def __init__(self, charm: ops.CharmBase, address: Optional[str] = None, key: Optional[str] = None,
                 endpoint: str = DEFAULT_ENDPOINT_NAME):
        super().__init__(charm, key or endpoint)
        self._charm = charm
        self.juju_topology = {
            "unit": self.model.unit.name,
            "model": self.model.name
        }
        self.address = address or socket.getfqdn()
        relation = self.model.get_relation(endpoint)
        # filter out common unhappy relation states
        self.relation: Optional[ops.Relation] = relation if relation and relation.app and relation.data else None

    def publish_unit_address(self, url: str):
        """Publish this unit's URL via the unit databag."""

        try:
            urlparse(url)
        except Exception as e:
            raise ValueError(f"{url} is an invalid url") from e

        databag_model = MimirClusterRequirerUnitData(
            juju_topology=self.juju_topology,
            address=url,
        )
        relation = self.relation
        if relation:
            unit_databag = relation.data[self.model.unit]
            databag_model.dump(unit_databag)

    def publish_app_roles(self, roles: Dict[MimirRole, int]):
        """Publish this application's roles via the application databag."""
        if not self._charm.unit.is_leader():
            raise DatabagAccessPermissionError("only the leader unit can publish roles.")

        relation = self.relation
        if relation:
            databag_model = MimirClusterRequirerAppData(roles=roles)
            databag_model.dump(relation.data[self.model.app])

    def get_mimir_config(self) -> Dict[str, Any]:
        """Fetch the mimir config from the coordinator databag."""
        data = {}
        relation = self.relation
        if relation:
            coordinator_databag = MimirClusterProviderAppData.load(relation.data[relation.app])
            data = coordinator_databag.mimir_config
        return data
