#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

from mimir_cluster import (
    MIMIR_CERT_FILE,
    MIMIR_CLIENT_CA_FILE,
    MIMIR_KEY_FILE,
    MimirClusterProvider,
    MimirRole,
)
from mimir_config import _S3ConfigData

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

# The minimum number of workers per role to enable replication
REPLICATION_MIN_WORKERS = 3
# The default amount of replicas to set when there are enough workers per role;
# otherwise, replicas will be "disabled" by setting the amount to 1
DEFAULT_REPLICATION = 3


class MimirCoordinator:
    """Mimir coordinator."""

    def __init__(
        self,
        cluster_provider: MimirClusterProvider,
        # TODO: use and import tls requirer obj
        tls_requirer: Any = None,
        # TODO: use and import s3 requirer obj
        s3_requirer: Any = None,
        # root and recovery data need to be in distinct directories
        root_data_dir: Path = Path("/data"),
        recovery_data_dir: Path = Path("/recovery-data"),
    ):
        self._cluster_provider = cluster_provider
        self._s3_requirer = s3_requirer  # type: ignore
        self._tls_requirer = tls_requirer  # type: ignore
        self._root_data_dir = root_data_dir
        self._recovery_data_dir = recovery_data_dir

    def is_coherent(self) -> bool:
        """Return True if the roles list makes up a coherent mimir deployment."""
        roles: Iterable[MimirRole] = self._cluster_provider.gather_roles().keys()
        return set(roles).issuperset(MINIMAL_DEPLOYMENT)

    def missing_roles(self) -> Set[MimirRole]:
        """If the coordinator is incoherent, return the roles that are missing for it to become so."""
        roles: Iterable[MimirRole] = self._cluster_provider.gather_roles().keys()
        return set(MINIMAL_DEPLOYMENT).difference(roles)

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

    def build_config(
        self, s3_config_data: Optional[_S3ConfigData], tls_enabled: bool = False
    ) -> Dict[str, Any]:
        """Generate shared config file for mimir.

        Reference: https://grafana.com/docs/mimir/latest/configure/
        """
        mimir_config: Dict[str, Any] = {
            "common": {},
            "alertmanager": self._build_alertmanager_config(),
            "alertmanager_storage": self._build_alertmanager_storage_config(),
            "compactor": self._build_compactor_config(),
            "ingester": self._build_ingester_config(),
            "ruler": self._build_ruler_config(),
            "ruler_storage": self._build_ruler_storage_config(),
            "store_gateway": self._build_store_gateway_config(),
            "blocks_storage": self._build_blocks_storage_config(),
            "memberlist": self._build_memberlist_config(),
        }

        if s3_config_data:
            mimir_config["common"]["storage"] = self._build_s3_storage_config(s3_config_data)
            self._update_s3_storage_config(mimir_config["blocks_storage"], "blocks")
            self._update_s3_storage_config(mimir_config["ruler_storage"], "rules")
            self._update_s3_storage_config(mimir_config["alertmanager_storage"], "alerts")

        # todo: TLS config for memberlist
        if tls_enabled:
            mimir_config["server"] = self._build_tls_config()

        return mimir_config

    def _build_tls_config(self) -> Dict[str, Any]:
        tls_config = {
            "cert_file": MIMIR_CERT_FILE,
            "key_file": MIMIR_KEY_FILE,
            "client_ca_file": MIMIR_CLIENT_CA_FILE,
            "client_auth_type": "RequestClientCert",
        }
        return {
            "http_tls_config": tls_config,
            "grpc_tls_config": tls_config,
        }

    # data_dir:
    # The Mimir Alertmanager stores the alerts state on local disk at the location configured using -alertmanager.storage.path.
    # Should be persisted if not replicated

    # sharding_ring.replication_factor: int
    # (advanced) The replication factor to use when sharding the alertmanager.
    def _build_alertmanager_config(self) -> Dict[str, Any]:
        alertmanager_scale = len(
            self._cluster_provider.gather_addresses_by_role().get(MimirRole.alertmanager, [])
        )
        return {
            "data_dir": str(self._root_data_dir / "data-alertmanager"),
            "sharding_ring": {
                "replication_factor": (
                    1 if alertmanager_scale < REPLICATION_MIN_WORKERS else DEFAULT_REPLICATION
                )
            },
        }

    # filesystem: dir
    # The Mimir Alertmanager also periodically stores the alert state in the storage backend configured with -alertmanager-storage.backend (For Recovery)
    def _build_alertmanager_storage_config(self) -> Dict[str, Any]:
        return {
            "filesystem": {
                "dir": str(self._recovery_data_dir / "data-alertmanager"),
            },
        }

    # data_dir:
    # Directory to temporarily store blocks during compaction.
    # This directory is not required to be persisted between restarts.
    def _build_compactor_config(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self._root_data_dir / "data-compactor"),
        }

    # ring.replication_factor: int
    # Number of ingesters that each time series is replicated to. This option
    # needs be set on ingesters, distributors, queriers and rulers when running in
    # microservices mode.
    def _build_ingester_config(self) -> Dict[str, Any]:
        ingester_scale = len(
            self._cluster_provider.gather_addresses_by_role().get(MimirRole.ingester, [])
        )
        return {
            "ring": {
                "replication_factor": (
                    1 if ingester_scale < REPLICATION_MIN_WORKERS else DEFAULT_REPLICATION
                )
            }
        }

    # rule_path:
    # Directory to store temporary rule files loaded by the Prometheus rule managers.
    # This directory is not required to be persisted between restarts.
    def _build_ruler_config(self) -> Dict[str, Any]:
        return {
            "rule_path": str(self._root_data_dir / "data-ruler"),
        }

    # sharding_ring.replication_factor:
    # (advanced) The replication factor to use when sharding blocks. This option
    # needs be set both on the store-gateway, querier and ruler when running in
    # microservices mode.
    def _build_store_gateway_config(self) -> Dict[str, Any]:
        store_gateway_scale = len(
            self._cluster_provider.gather_addresses_by_role().get(MimirRole.store_gateway, [])
        )
        return {
            "sharding_ring": {
                "replication_factor": (
                    1 if store_gateway_scale < REPLICATION_MIN_WORKERS else DEFAULT_REPLICATION
                )
            }
        }

    # filesystem: dir
    # Storage backend reads Prometheus recording rules from the local filesystem.
    # The ruler looks for tenant rules in the self._root_data_dir/rules/<TENANT ID> directory. The ruler requires rule files to be in the Prometheus format.
    def _build_ruler_storage_config(self) -> Dict[str, Any]:
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
    def _build_blocks_storage_config(self) -> Dict[str, Any]:
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

    def _build_s3_storage_config(self, s3_config_data: _S3ConfigData) -> Dict[str, Any]:
        return {
            "backend": "s3",
            "s3": s3_config_data.model_dump(),
        }

    def _update_s3_storage_config(self, storage_config: Dict[str, Any], prefix_name: str) -> None:
        """Update S3 storage configuration in `storage_config`.

        If the key 'filesystem' is present in `storage_config`, remove it and add a new key
        'storage_prefix' with the value of `prefix_name` for the S3 bucket.
        """
        if "filesystem" in storage_config:
            storage_config.pop("filesystem")
            storage_config["storage_prefix"] = prefix_name

    # cluster_label:
    # (advanced) The cluster label is an optional string to include in outbound
    # packets and gossip streams. Other members in the memberlist cluster will
    # discard any message whose label doesn't match the configured one, unless the
    def _build_memberlist_config(self) -> Dict[str, Any]:
        coordinator = self._cluster_provider._charm
        return {
            "cluster_label": f"{coordinator.model.name}_{coordinator.model.uuid}_{coordinator.app.name}",
            "join_members": list(self._cluster_provider.gather_addresses()),
        }
