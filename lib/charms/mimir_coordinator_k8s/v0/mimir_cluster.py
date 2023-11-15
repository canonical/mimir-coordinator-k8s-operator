"""This file defines the schemas for the provider and requirer sides of this relation interface.

It must expose two interfaces.schema_base.DataBagSchema subclasses called:
- ProviderSchema
- RequirerSchema

TODO: see https://github.com/canonical/charm-relation-interfaces/issues/121
"""
import json
import logging
from collections import Counter
from enum import Enum
from typing import Dict, List, MutableMapping, Union
from typing import Optional

import pydantic
from ops import Object
from pydantic import BaseModel
from pydantic import Json

log = logging.getLogger("mimir_cluster")
LIBID = "9818a8d44028454a94c6c3a01f4316d2"

LIBAPI = 0
LIBPATCH = 0

BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}


class DataValidationError(Exception):
    """Raised when relation databag validation fails."""


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


class S3Config(pydantic.BaseModel):
    url: str
    endpoint: str
    secret_key: str
    access_key: str
    insecure: bool


class MyProviderAppDataBag(DatabagModel):
    hash_ring: Json[List[str]]
    s3_config: Json[S3Config]
    config: Json[Dict[str, str]]


class ProviderSchema(DataBagSchema):
    """The schema for the provider side of this interface."""
    app: MyProviderAppDataBag


class JujuTopology(pydantic.BaseModel):
    model: str
    unit: str
    # ...


class MyRequirerUnitDataBag(DatabagModel):
    juju_topology: Json[JujuTopology]


class MyRequirerAppDataBag(DatabagModel):
    roles: Dict[MimirRole, int]


class RequirerSchema(DataBagSchema):
    """The schema for the requirer side of this interface."""
    unit: MyRequirerUnitDataBag
    app: MyRequirerAppDataBag



class MimirClusterProvider(Object):
    def __init__(self, parent, key: Optional[str]):
        super().__init__(parent, key)

    def populate_databags(self):
        databag_model = MyProviderAppDataBag(
            hash_ring={"foo": "bar"},
            s3_config=10,
            config="some config",
        )

        relation = self.model.get_relation("mimir_cluster")
        app_databag = relation.data[self.model.app]
        databag_model.dump(app_databag)  # write to local app databag

    def gather_roles(self) -> Dict[MimirRole, int]:
        """Go through the worker's app databags and sum the available roles."""
        data = Counter()
        for relation in self.model.relations["mimir_cluster"]:
            data.update(MyRequirerAppDataBag.load(relation.data[relation.app]))
        return {MimirRole(role): role_n for role, role_n in data.items()}


class MimirClusterRequirer(Object):
    def __init__(self, parent, key: Optional[str]):
        super().__init__(parent, key)

    def publish_address_to_unit_databag(self, *address):
        # TODO luca homework: add address info to unit databag
        pass

    def publish_roles(self, roles: Dict[MimirRole, int]):
        relation = self.model.get_relation("mimir_cluster")
        db = MyRequirerAppDataBag(roles={role.value: role_n for role, role_n in roles.items()})
        db.dump(relation.data[self.model.app])

    def get_configs(self):
        relation = self.model.get_relation("mimir_cluster")

        # read from remote app databag
        provider_data = MyProviderAppDataBag.load(relation.data[relation.app])

        # TODO do whatever with these
        hash_ring = provider_data.hash_ring
        s3 = provider_data.s3_config
        config = provider_data.config

