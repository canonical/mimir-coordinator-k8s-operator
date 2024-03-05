#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""This module contains an endpoint wrapper class for the provider side of the ``mimir-cluster`` relation.

As this relation is cluster-internal and not intended for third-party charms to interact with `mimir-coordinator-k8s`, its only user will be the mimir-coordinator-k8s charm. As such, it does not live in a charm lib as most other relation endpoint wrappers do.
"""


import json
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Set

import ops
import pydantic
from ops import Object
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("mimir_cluster")

DEFAULT_ENDPOINT_NAME = "mimir-cluster"
BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}
MIMIR_CONFIG_FILE = "/etc/mimir/mimir-config.yaml"
MIMIR_CERT_FILE = "/etc/mimir/server.cert"
MIMIR_KEY_FILE = "/etc/mimir/private.key"
MIMIR_CLIENT_CA_FILE = "/etc/mimir/ca.cert"


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
    MimirRole.backend: (
        MimirRole.store_gateway,
        MimirRole.compactor,
        MimirRole.ruler,
        MimirRole.alertmanager,
        MimirRole.query_scheduler,
        MimirRole.overrides_exporter,
    ),
    MimirRole.all: list(MimirRole),
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


class MimirClusterProvider(Object):
    """``mimir-cluster`` provider endpoint wrapper."""

    def __init__(
        self,
        charm: ops.CharmBase,
        key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT_NAME,
    ):
        super().__init__(charm, key)
        self._charm = charm
        self._relations = self.model.relations[endpoint]

    def publish_data(
        self,
        mimir_config: Dict[str, Any],
        loki_endpoints: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish the mimir config and loki endpoints to all related mimir worker clusters."""
        for relation in self._relations:
            if relation:
                local_app_databag = MimirClusterProviderAppData(
                    mimir_config=mimir_config, loki_endpoints=loki_endpoints
                )
                local_app_databag.dump(relation.data[self.model.app])

    def gather_roles(self) -> Dict[MimirRole, int]:
        """Go through the worker's app databags and sum the available application roles."""
        data = {}
        for relation in self._relations:
            if relation.app:
                remote_app_databag = relation.data[relation.app]
                try:
                    worker_roles: List[MimirRole] = MimirClusterRequirerAppData.load(
                        remote_app_databag
                    ).roles
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

    def get_datasource_address(self) -> Optional[str]:
        """Get datasource address."""
        addresses_by_role = self.gather_addresses_by_role()
        if address_set := addresses_by_role.get("ruler", None):
            return address_set.pop()

    def gather_topology(self) -> List[Dict[str, str]]:
        """Gather Topology."""
        data = []
        for relation in self._relations:
            if not relation.app:
                continue

            for worker_unit in relation.units:
                try:
                    worker_data = MimirClusterRequirerUnitData.load(relation.data[worker_unit])
                    unit_address = worker_data.address
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    continue
                worker_topology = {
                    "unit": worker_unit.name,
                    "app": worker_unit.app.name,
                    "address": unit_address,
                }
                data.append(worker_topology)

        return data


class DatabagModel(BaseModel):
    """Base databag model."""

    model_config = ConfigDict(
        # Allow instantiating this class by field name (instead of forcing alias).
        populate_by_name=True,
        # Custom config key: whether to nest the whole datastructure (as json)
        # under a field or spread it out at the toplevel.
        _NEST_UNDER=None,
    )  # type: ignore
    """Pydantic config."""

    @classmethod
    def load(cls, databag: MutableMapping[str, str]):
        """Load this model from a Juju databag."""
        nest_under = cls.model_config.get("_NEST_UNDER")
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
        nest_under = self.model_config.get("_NEST_UNDER")
        if nest_under:
            databag[nest_under] = self.json()

        dct = self.model_dump(by_alias=True)
        for key, field in self.model_fields.items():  # type: ignore
            value = dct[key]
            databag[field.alias or key] = json.dumps(value)
        return databag


class JujuTopology(pydantic.BaseModel):
    """JujuTopology."""

    model: str
    unit: str
    # ...


class MimirClusterProviderAppData(DatabagModel):
    """MimirClusterProviderAppData."""

    mimir_config: Dict[str, Any]
    loki_endpoints: Optional[Dict[str, str]] = None
    # todo: validate with
    #  https://grafana.com/docs/mimir/latest/configure/about-configurations/#:~:text=Validate%20a%20configuration,or%20in%20a%20CI%20environment.
    #  caveat: only the requirer node can do it


class MimirClusterRequirerAppData(DatabagModel):
    """MimirClusterRequirerAppData."""

    roles: List[MimirRole]


class MimirClusterRequirerUnitData(DatabagModel):
    """MimirClusterRequirerUnitData."""

    juju_topology: JujuTopology
    address: str


class MimirClusterError(Exception):
    """Base class for exceptions raised by this module."""


class DataValidationError(MimirClusterError):
    """Raised when relation databag validation fails."""


class DatabagAccessPermissionError(MimirClusterError):
    """Raised when a follower attempts to write leader settings."""
