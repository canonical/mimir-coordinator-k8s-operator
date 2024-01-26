"""This file defines the schemas for the provider and requirer sides of this relation interface.

It must expose two interfaces.schema_base.DataBagSchema subclasses called:
- ProviderSchema
- RequirerSchema

TODO: see https://github.com/canonical/charm-relation-interfaces/issues/121
"""
import json
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Dict, MutableMapping, Set, List, Iterable
from typing import Optional
from urllib.parse import urlparse

import ops
import pydantic
from ops import Object, ObjectEvents, EventSource, RelationCreatedEvent
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("mimir_cluster")
LIBID = "9818a8d44028454a94c6c3a01f4316d2"
DEFAULT_ENDPOINT_NAME = "mimir-cluster"

LIBAPI = 0
LIBPATCH = 3

BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}

MIMIR_CONFIG_FILE = "/etc/mimir/mimir-config.yaml"
MIMIR_CERT_FILE = "/etc/mimir/server.cert"
MIMIR_KEY_FILE = "/etc/mimir/private.key"
MIMIR_CLIENT_CA_FILE = "/etc/mimir/ca.cert"


class MimirClusterError(Exception):
    """Base class for exceptions raised by this module."""


class DataValidationError(MimirClusterError):
    """Raised when relation databag validation fails."""


class DatabagAccessPermissionError(MimirClusterError):
    """Raised when a follower attempts to write leader settings."""


class DatabagModel(BaseModel):
    """Base databag model."""
    model_config = ConfigDict(
        # Allow instantiating this class by field name (instead of forcing alias).
        populate_by_name=True,
        # Custom config key: whether to nest the whole datastructure (as json)
        # under a field or spread it out at the toplevel.
        _NEST_UNDER=None)  # type: ignore
    """Pydantic config."""

    @classmethod
    def load(cls, databag: MutableMapping[str, str]):
        """Load this model from a Juju databag."""
        nest_under = cls.model_config.get('_NEST_UNDER')
        if nest_under:
            return cls.parse_obj(json.loads(databag[nest_under]))

        try:
            data = {k: json.loads(v) for k, v in databag.items() if k not in BUILTIN_JUJU_KEYS}
        except json.JSONDecodeError as e:
            msg = f"invalid databag contents: expecting json. {databag}"
            log.info(msg)
            raise DataValidationError(msg) from e

        try:
            return cls.parse_raw(json.dumps(data))  # type: ignore
        except pydantic.ValidationError as e:
            msg = f"failed to validate databag: {databag}"
            log.info(msg, exc_info=True)
            raise DataValidationError(msg) from e

    def dump(self, databag: Optional[MutableMapping[str, str]] = None, clear: bool = True):
        """Write the contents of this model to Juju databag.

        :param databag: the databag to write the data to.
        :param clear: ensure the databag is cleared before writing it.
        """
        if clear and databag:
            databag.clear()

        if databag is None:
            databag = {}
        nest_under = self.model_config.get('_NEST_UNDER')
        if nest_under:
            databag[nest_under] = self.json()

        dct = self.model_dump(by_alias=True)
        for key, field in self.model_fields.items():  # type: ignore
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
    overrides_exporter = "overrides-exporter"
    query_scheduler = "query-scheduler"
    flusher = "flusher"
    query_frontend = "query-frontend"
    querier = "querier"
    store_gateway = "store-gateway"
    ingester = "ingester"
    distributor = "distributor"
    ruler = "ruler"
    alertmanager = "alertmanager"
    compactor = "compactor"

    # meta-roles
    read = "read"
    write = "write"
    backend = "backend"
    all = "all"


META_ROLES = {
    MimirRole.read: (MimirRole.query_frontend, MimirRole.querier),
    MimirRole.write: (MimirRole.distributor, MimirRole.ingester),
    MimirRole.backend: (MimirRole.store_gateway,
                        MimirRole.compactor,
                        MimirRole.ruler,
                        MimirRole.alertmanager,
                        MimirRole.query_scheduler,
                        MimirRole.overrides_exporter),
    MimirRole.all: list(MimirRole)
}


def expand_roles(roles: Iterable[MimirRole]) -> Set[MimirRole]:
    """Expand any meta roles to their 'atomic' equivalents."""
    expanded_roles = set()
    for role in roles:
        if role in META_ROLES:
            expanded_roles.update(META_ROLES[role])
        else:
            expanded_roles.add(role)
    return expanded_roles


class MimirClusterProviderAppData(DatabagModel):
    mimir_config: Dict[str, Any]
    loki_endpoints: Optional[Dict[str, str]] = None
    # todo: validate with
    #  https://grafana.com/docs/mimir/latest/configure/about-configurations/#:~:text=Validate%20a%20configuration,or%20in%20a%20CI%20environment.
    #  caveat: only the requirer node can do it


class ProviderSchema(DataBagSchema):
    """The schema for the provider side of this interface."""
    app: MimirClusterProviderAppData  # pyright: ignore[reportIncompatibleVariableOverride]


class JujuTopology(pydantic.BaseModel):
    model: str
    unit: str
    # ...


class MimirClusterRequirerUnitData(DatabagModel):
    juju_topology: JujuTopology
    address: str


class MimirClusterRequirerAppData(DatabagModel):
    roles: List[MimirRole]


class RequirerSchema(DataBagSchema):
    """The schema for the requirer side of this interface."""
    unit: MimirClusterRequirerUnitData  # pyright: ignore[reportIncompatibleVariableOverride]
    app: MimirClusterRequirerAppData  # pyright: ignore[reportIncompatibleVariableOverride]


class MimirClusterProvider(Object):
    def __init__(self, charm: ops.CharmBase, key: Optional[str] = None,
                 endpoint: str = DEFAULT_ENDPOINT_NAME):
        super().__init__(charm, key)
        self._charm = charm
        self._relations = self.model.relations[endpoint]

    def publish_data(self,
                     mimir_config: Dict[str, Any],
                     loki_endpoints: Optional[Dict[str, str]] = None,
                     ) -> None:
        """Publish the mimir config and loki endpoints to all related mimir worker clusters."""
        for relation in self._relations:
            if relation:
                local_app_databag = MimirClusterProviderAppData(mimir_config=mimir_config,
                                                                loki_endpoints=loki_endpoints)
                local_app_databag.dump(relation.data[self.model.app])

    def gather_roles(self) -> Dict[MimirRole, int]:
        """Go through the worker's app databags and sum the available application roles."""
        data = {}
        for relation in self._relations:
            if relation.app:
                remote_app_databag = relation.data[relation.app]
                try:
                    worker_roles: List[MimirRole] = MimirClusterRequirerAppData.load(remote_app_databag).roles
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    worker_roles = []

                # the number of units with each role is the number of remote units
                role_n = len(relation.units)  # exclude this unit

                for role in expand_roles(worker_roles):
                    if role not in data:
                        data[role] = 0
                    data[role] += role_n
        return data

    def gather_addresses_by_role(self) -> Dict[str, Set[str]]:
        """Go through the worker's unit databags to collect all the addresses published by the units, by role."""
        data = defaultdict(set)
        for relation in self._relations:

            if not relation.app:
                log.debug(f"skipped {relation} as .app is None")
                continue

            try:
                worker_app_data = MimirClusterRequirerAppData.load(relation.data[relation.app])
                worker_roles = set(worker_app_data.roles)
            except DataValidationError as e:
                log.info(f"invalid databag contents: {e}")
                continue

            for worker_unit in relation.units:
                try:
                    worker_data = MimirClusterRequirerUnitData.load(relation.data[worker_unit])
                    unit_address = worker_data.address
                    for role in worker_roles:
                        data[role].add(unit_address)
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    continue

        return data

    def gather_addresses(self) -> Set[str]:
        """Go through the worker's unit databags to collect all the addresses published by the units."""
        data = set()
        addresses_by_role = self.gather_addresses_by_role()
        for role, address_set in addresses_by_role.items():
            data.update(address_set)

        return data


class MimirClusterRemovedEvent(ops.EventBase):
    """Event emitted when the relation with the "mimir-cluster" provider has been severed.

    Or when the relation data has been wiped.
    """


class ConfigReceivedEvent(ops.EventBase):
    """Event emitted when the "mimir-cluster" provider has shared a new mimir config."""
    config: Dict[str, Any]
    """The mimir config."""

    def __init__(self, handle: ops.framework.Handle, config: Dict[str, Any]):
        super().__init__(handle)
        self.config = config

    def snapshot(self) -> Dict[str, Any]:
        """Used by the framework to serialize the event to disk.

        Not meant to be called by charm code.
        """
        return {'config': json.dumps(self.config)}

    def restore(self, snapshot: Dict[str, Any]):
        """Used by the framework to deserialize the event from disk.

        Not meant to be called by charm code.
        """
        self.relation = json.loads(snapshot['config'])


class MimirClusterRequirerEvents(ObjectEvents):
    """Events emitted by the MimirClusterRequirer "mimir-cluster" endpoint wrapper."""
    config_received = EventSource(ConfigReceivedEvent)
    created = EventSource(RelationCreatedEvent)
    removed = EventSource(MimirClusterRemovedEvent)


class MimirClusterRequirer(Object):
    on = MimirClusterRequirerEvents()  # type: ignore

    def __init__(self, charm: ops.CharmBase, key: Optional[str] = None,
                 endpoint: str = DEFAULT_ENDPOINT_NAME):
        super().__init__(charm, key or endpoint)
        self._charm = charm
        self.juju_topology = {
            "unit": self.model.unit.name,
            "model": self.model.name
        }
        relation = self.model.get_relation(endpoint)
        # filter out common unhappy relation states
        self.relation: Optional[ops.Relation] = relation if relation and relation.app and relation.data else None

        self.framework.observe(self._charm.on[endpoint].relation_changed,
                               self._on_mimir_cluster_relation_changed)
        self.framework.observe(self._charm.on[endpoint].relation_created,
                               self._on_mimir_cluster_relation_created)
        self.framework.observe(self._charm.on[endpoint].relation_broken,
                               self._on_mimir_cluster_relation_broken)

    def _on_mimir_cluster_relation_broken(self, _event: ops.RelationBrokenEvent):
        self.on.removed.emit()

    def _on_mimir_cluster_relation_created(self, event: ops.RelationCreatedEvent):
        self.on.created.emit(relation=event.relation, app=event.app, unit=event.unit)

    def _on_mimir_cluster_relation_changed(self, _event: ops.RelationChangedEvent):
        # to prevent the event from firing if the relation is in an unhealthy state (breaking...)
        if self.relation:
            new_config = self.get_mimir_config()
            if new_config:
                self.on.config_received.emit(new_config)

            # if we have published our data, but we receive an empty/invalid config,
            # then the remote end must have removed it.
            elif self.is_published():
                self.on.removed.emit()

    def is_published(self):
        """Verify that the local side has done all they need to do.

        - unit address is published
        - roles are published
        """
        relation = self.relation
        if not relation:
            return False

        unit_data = relation.data[self._charm.unit]
        app_data = relation.data[self._charm.app]

        try:
            MimirClusterRequirerUnitData.load(unit_data)
            MimirClusterRequirerAppData.load(app_data)
        except DataValidationError as e:
            log.info(f"invalid databag contents: {e}")
            return False
        return True

    def publish_unit_address(self, url: str):
        """Publish this unit's URL via the unit databag."""

        try:
            urlparse(url)
        except Exception as e:
            raise ValueError(f"{url} is an invalid url") from e

        databag_model = MimirClusterRequirerUnitData(
            juju_topology=self.juju_topology,  # type: ignore
            address=url,
        )
        relation = self.relation
        if relation:
            unit_databag = relation.data[self.model.unit]  # type: ignore # all checks are done in __init__
            databag_model.dump(unit_databag)

    def publish_app_roles(self, roles: Iterable[MimirRole]):
        """Publish this application's roles via the application databag."""
        if not self._charm.unit.is_leader():
            raise DatabagAccessPermissionError("only the leader unit can publish roles.")

        relation = self.relation
        if relation:
            deduplicated_roles = list(expand_roles(roles))
            databag_model = MimirClusterRequirerAppData(roles=deduplicated_roles)
            databag_model.dump(relation.data[self.model.app])

    def _get_data_from_coordinator(self) -> Optional[MimirClusterProviderAppData]:
        """Fetch the contents of the doordinator databag."""
        data: Optional[MimirClusterProviderAppData] = None
        relation = self.relation
        if relation:
            try:
                databag = relation.data[relation.app]  # type: ignore # all checks are done in __init__
                coordinator_databag = MimirClusterProviderAppData.load(databag)
                data = coordinator_databag
            except DataValidationError as e:
                log.info(f"invalid databag contents: {e}")

        return data

    def get_mimir_config(self) -> Dict[str, Any]:
        """Fetch the mimir config from the coordinator databag."""
        data = self._get_data_from_coordinator()
        if data:
            return data.mimir_config
        return {}

    def get_loki_endpoints(self) -> Dict[str, str]:
        """Fetch the loki endpoints from the coordinator databag."""
        data = self._get_data_from_coordinator()
        if data:
            return data.loki_endpoints or {}
        return {}

    def get_cert_secret_ids(self) -> Optional[str]:
        """Fetch certificates secrets ids for the mimir config."""
        if self.relation and self.relation.app:
            return self.relation.data[self.relation.app].get("secrets", None)
