#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable

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
        root_data_dir: Path = Path("/etc/mimir"),
    ):
        self._cluster_provider = cluster_provider
        self._s3_requirer = s3_requirer  # type: ignore
        self._tls_requirer = tls_requirer  # type: ignore
        self._root_data_dir = root_data_dir

    def is_coherent(self) -> bool:
        """Return True if the roles list makes up a coherent mimir deployment."""
        roles: Iterable[MimirRole] = self._cluster_provider.gather_roles().keys()
        return set(roles).issuperset(MINIMAL_DEPLOYMENT)
    
    def is_scaled(self) -> bool:
        """Return True if more than 1 worker are forming the mimir cluster"""
        return len(list(self._cluster_provider.gather_addresses())) > 1

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

    def build_config(self, s3_data: dict) -> Dict[str, Any]:
        """Generate shared config file for mimir.

        Reference: https://grafana.com/docs/mimir/latest/configure/
        """
        mimir_config: Dict[str, Any] = {
            "common": {},
            "alertmanager": self.build_alertmanager_config(),
            "alertmanager_storage": self.build_alertmanager_storage_config(),
            "compactor": self.build_compactor_config(),
            "ruler": self.build_ruler_config(),
            "ruler_storage": self.build_ruler_storage_config(),
            "blocks_storage": self.build_blocks_storage_config(),
        }

        if s3_data:
            mimir_config["common"]["storage"] = self.build_s3_storage_config(s3_data)
            self.update_s3_storage_config(mimir_config["blocks_storage"], "filesystem", "blocks")
            self.update_s3_storage_config(mimir_config["ruler_storage"], "filesystem", "rules")
            self.update_s3_storage_config(
                mimir_config["alertmanager_storage"], "filesystem", "alerts"
            )

        mimir_config["memberlist"] = self.build_memberlist_config()

        if self._tls_requirer:
            mimir_config.update(self.build_tls_config())

        return mimir_config

    # data_dir:
    # The Mimir Alertmanager stores the alerts state on local disk at the location configured using -alertmanager.storage.path.
    # Should be persisted if not replicated
    def build_alertmanager_config(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self._root_data_dir / "data-alertmanager"),
        }

    # filesystem: dir
    # The Mimir Alertmanager also periodically stores the alert state in the storage backend configured with -alertmanager-storage.backend (For Recovery)
    def build_alertmanager_storage_config(self) -> Dict[str, Any]:
        return {
            "filesystem": {
                "dir": str(self._root_data_dir / "data-alertmanager-recovery"),
            },
        }

    # data_dir:
    # Directory to temporarily store blocks during compaction.
    # This directory is not required to be persisted between restarts.
    def build_compactor_config(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self._root_data_dir / "data-compactor"),
        }

    # rule_path:
    # Directory to store temporary rule files loaded by the Prometheus rule managers.
    # This directory is not required to be persisted between restarts.
    def build_ruler_config(self) -> Dict[str, Any]:
        return {
            "rule_path": str(self._root_data_dir / "data-ruler"),
        }

    # filesystem: dir
    # Storage backend reads Prometheus recording rules from the local filesystem.
    # The ruler looks for tenant rules in the self._root_data_dir/rules/<TENANT ID> directory. The ruler requires rule files to be in the Prometheus format.
    def build_ruler_storage_config(self) -> Dict[str, Any]:
        return {
            "filesystem": {
                "dir": str(self._root_data_dir / "rules"),
            },
        }

    # bucket_store: sync_dir
    # Directory to store synchronized TSDB index headers. This directory is not
    # required to be persisted between restarts, but it's highly recommended

    # filesystem: dir
    # Mimir upload blocks (of metrics) to the object storage at period interval.

    # tsdb: dir
    # Directory to store TSDBs (including WAL) in the ingesters.
    #  This directory is required to be persisted between restarts.

    # The TSDB dir is used by ingesters, while the filesystem: dir is the "object storage"
    # Ingesters are expected to upload TSDB blocks to filesystem: dir every 2h.
    def build_blocks_storage_config(self) -> Dict[str, Any]:
        return {
            "bucket_store": {
                "sync_dir": str(self._root_data_dir / "tsdb-sync"),
            },
            "filesystem": {
                "dir": str(self._root_data_dir / "blocks"),
            },
            "tsdb": {
                "dir": str(self._root_data_dir / "tsdb"),
            },
        }

    def build_s3_storage_config(self, s3_data: dict) -> Dict[str, Any]:
        return {
            "backend": "s3",
            "s3": {
                "endpoint": f'{s3_data["service"]}.{s3_data["namespace"]}.svc.cluster.local:{s3_data["port"]}',
                "access_key_id": s3_data["access_key"],
                "secret_access_key": s3_data["secret_key"],
                "insecure": not s3_data["secure"],
                "bucket_name": "mimir",
            },
        }

    def update_s3_storage_config(
        self, storage_config: Dict[str, Any], old_key: str, prefix_name: str
    ) -> None:
        if old_key in storage_config:
            storage_config.pop(old_key)
            storage_config["storage_prefix"] = prefix_name

    def build_memberlist_config(self) -> Dict[str, Any]:
        return {"join_members": list(self._cluster_provider.gather_addresses())}

    def build_tls_config(self) -> Dict[str, Any]:
        return {
            "tls_enabled": True,
            "tls_cert_path": self._tls_requirer.cacert,
            "tls_key_path": self._tls_requirer.key,
            "tls_ca_path": self._tls_requirer.capath,
        }
