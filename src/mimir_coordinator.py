#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import json
import logging
from collections import Counter
from typing import List, Optional

import pydantic
from interfaces.mimir_worker.v0.schema import MimirRole, RequirerSchema
from ops.model import ModelError, Relation, Unit

logger = logging.getLogger(__name__)

MINIMAL_DEPLOYMENT = {
    # from official docs:
    MimirRole.compactor: 1,
    MimirRole.distributor: 1,
    MimirRole.ingester: 1,
    MimirRole.querier: 1,
    MimirRole.query_frontend: 1,
    MimirRole.store_gateway: 1,
    # we add:
    MimirRole.ruler: 1,
    MimirRole.alertmanager: 1,
}
"""The minimal set of roles that need to be allocated for the
deployment to be considered consistent (otherwise we set blocked). On top of what mimir itself lists as required,
we add alertmanager."""

RECOMMENDED_DEPLOYMENT = {
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
"""The set of roles that need to be allocated for the
deployment to be considered robust according to the official recommendations/guidelines."""


class MimirCoordinator:
    """Mimir coordinator."""

    def __init__(self, relations: List[Relation]):
        self.relations = relations

    def is_coherent(self):
        """Return True if the roles list makes up a coherent mimir deployment."""
        roles = self.roles()
        return roles and set(roles).issubset(MINIMAL_DEPLOYMENT)

    def is_recommended(self) -> bool:
        """Return True if is a subset of the minimal deployment."""
        roles = self.roles()
        return bool(roles) and set(roles).issubset(MINIMAL_DEPLOYMENT)

    def roles(self) -> Counter:
        """Gather the roles from the mimir_worker relations and count them."""
        roles = Counter()

        for relation in self.relations:
            if not self._relation_data_valid(relation):
                logger.error(f"Invalid relation data in {relation!r}")
                continue

            try:
                raw_roles = json.loads(relation.data[relation.app]["roles"])
            except (KeyError, ModelError):
                logger.error(f"Could not load roles from relation {relation!r}", exc_info=True)
                continue
            roles.update(raw_roles)

        return roles

    def _relation_data_valid(self, relation: Relation, unit: Optional[Unit] = None) -> bool:
        """Check that the relation data is valid."""
        schema = RequirerSchema
        units_to_check = [unit] if unit else relation.units
        for unit in units_to_check:
            try:
                schema().validate(
                    {"app": relation.data[relation.app], "unit": relation.data[unit]}
                )
            except (pydantic.ValidationError, ModelError, KeyError):
                logger.error(f"relation data invalid: {relation.data}", exc_info=True)
                return False
        return True
