#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import logging
from collections import Counter
from typing import Any, Dict

from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider, MimirRole

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


class MimirCoordinator:
    """Mimir coordinator."""

    def __init__(
        self,
        cluster_provider: MimirClusterProvider,
        # TODO: use and import tls requirer obj
        tls_requirer: Any = None,
        # TODO: use and import s3 requirer obj
        s3_requirer: Any = None,
    ):
        self._cluster_provider = cluster_provider
        self._s3_requirer = s3_requirer  # type: ignore
        self._tls_requirer = tls_requirer  # type: ignore

    def is_coherent(self) -> bool:
        """Return True if the roles list makes up a coherent mimir deployment."""
        roles: Dict[MimirRole, int] = self._cluster_provider.gather_roles()
        return set(roles).issuperset(MINIMAL_DEPLOYMENT)

    def is_recommended(self) -> bool:
        """Return True if is a superset of the minimal deployment.

        I.E. If all required roles are assigned, and each role has the recommended amount of units.
        """
        roles: Dict[MimirRole, int] = self._cluster_provider.gather_roles()
        # python>=3.11 would support roles >= RECOMMENDED_DEPLOYMENT
        for role, min_n in RECOMMENDED_DEPLOYMENT.items():
            if roles.get(role, 0) < min_n:
                return False
        return True

    def build_config(self, _charm_config: Dict[str, Any]) -> Dict[str, Any]:
        """Generate shared config file for mimir.

        Reference: https://grafana.com/docs/mimir/latest/configure/
        """
        mimir_config: Dict[str, Any] = {"common": {}}

        if self._s3_requirer:
            s3_config = self._s3_requirer.s3_config
            mimir_config["common"]["storage"] = {
                "backend": "s3",
                "s3": {
                    "region": s3_config.region,  # eg. 'us-west'
                    "bucket_name": s3_config.bucket_name,  # eg: 'mimir'
                },
            }
            mimir_config["blocks_storage"] = {
                "s3": {"bucket_name": s3_config.blocks_bucket_name}  # e.g. 'mimir-blocks'
            }

        # memberlist config for gossip and hash ring
        mimir_config["join_members"] = list(self._cluster_provider.gather_addresses())

        # todo: TLS config for memberlist
        if self._tls_requirer:
            mimir_config["tls_enabled"] = True
            mimir_config["tls_cert_path"] = self._tls_requirer.cacert
            mimir_config["tls_key_path"] = self._tls_requirer.key
            mimir_config["tls_ca_path"] = self._tls_requirer.capath

        return mimir_config
