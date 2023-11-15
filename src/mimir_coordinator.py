#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import logging
import typing
from collections import Counter
from typing import List, Optional

import pydantic
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirRole, RequirerSchema
from ops.model import ModelError, Relation, Unit

logger = logging.getLogger(__name__)

MINIMAL_DEPLOYMENT = {
    # from official docs:
    MimirRole.compactor: 1,
    MimirRole.distributor: 1,
    MimirRole.ingester: 1,
    MimirRole.querier: 1,
    MimirRole.query_frontend: 1,
    MimirRole.query_scheduler: 1,
    MimirRole.store_gateway: 1,
    # we add:
    MimirRole.ruler: 1,
    MimirRole.alertmanager: 1,
}
"""The minimal set of roles that need to be allocated for the
deployment to be considered consistent (otherwise we set blocked). On top of what mimir itself lists as required,
we add alertmanager."""

RECOMMENDED_DEPLOYMENT = Counter(
    {
        MimirRole.ingester: 3,
        MimirRole.querier: 2,
        MimirRole.query_scheduler: 2,
        MimirRole.alertmanager: 1,
        MimirRole.query_frontend: 1,
        MimirRole.ruler: 1,
        MimirRole.store_gateway: 1,
        MimirRole.compactor: 1,
        MimirRole.distributor: 1,
    }
)
"""The set of roles that need to be allocated for the
deployment to be considered robust according to the official recommendations/guidelines."""


def _relation_to_role(relation: Relation) -> MimirRole:
    # TODO: extract the role from the relation
    return MimirRole("whatever-role")  # FIXME


class MimirCoordinator:
    """Mimir coordinator."""

    def __init__(self, relations: List[Relation]):
        self.relations = relations

    def is_coherent(self):
        """Return True if the roles list makes up a coherent mimir deployment."""
        roles = self.roles()
        return set(roles).issuperset(MINIMAL_DEPLOYMENT)

    def is_recommended(self) -> bool:
        """Return True if is a superset of the minimal deployment.

        I.E. If all required roles are assigned, and each role has the recommended amount of units.
        """
        roles = self.roles()
        # python>=3.11 would support roles >= RECOMMENDED_DEPLOYMENT
        for role, min_n in RECOMMENDED_DEPLOYMENT.items():
            if roles.get(role, 0) < min_n:
                return False
        return True

    # todo move to a new mimir_cluster.MimirClusterProvider class
    def roles(self) -> typing.Counter[MimirRole]:
        """Gather the roles from the mimir_cluster relations and count them."""
        roles = Counter()

        for relation in self.relations:
            if not self._relation_data_valid(relation):
                logger.error("Invalid relation data in %s", relation)
                continue

            try:
                role = _relation_to_role(relation)  # TODO: get the role from relation data

            except ValueError:
                # TODO: not an actual role: should probably warn
                logger.info(f"Not a mimir-*role* relation: {relation.name}")
                continue

            roles[role] += len(relation.units)
        return roles

    def _relation_data_valid(self, relation: Relation, unit: Optional[Unit] = None) -> bool:
        """Check that the relation data is valid."""
        schema = RequirerSchema
        units_to_check = [unit] if unit else relation.units
        for unit in units_to_check:
            try:
                schema().validate(
                    {"app": relation.data[relation.app], "unit": relation.data[unit]}  # type: ignore
                )
            except (pydantic.ValidationError, ModelError, KeyError):
                logger.error(f"relation data invalid: {relation.data}", exc_info=True)
                return False
        return True
