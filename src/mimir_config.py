#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Mimir coordinator."""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import yaml
from cosl import JujuTopology
from cosl.coordinated_workers.coordinator import Coordinator
from cosl.coordinated_workers.interface import ClusterProvider
from cosl.coordinated_workers.worker import CERT_FILE, CLIENT_CA_FILE, KEY_FILE

logger = logging.getLogger(__name__)

ROLES = {
    "overrides-exporter",
    "query-scheduler",
    "flusher",
    "query-frontend",
    "querier",
    "store-gateway",
    "ingester",
    "distributor",
    "ruler",
    "alertmanager",
    "compactor",
    # meta-roles
    "read",
    "write",
    "backend",
    "all",
}
"""Mimir component role names."""

META_ROLES = {
    "read": {"query-frontend", "querier"},
    "write": {"distributor", "ingester"},
    "backend": {
        "store-gateway",
        "compactor",
        "ruler",
        "alertmanager",
        "query-scheduler",
        "overrides-exporter",
    },
    "all": set(ROLES) - {"read", "write", "backend", "all"},
}

MINIMAL_DEPLOYMENT = {
    # from official docs:
    "compactor",
    "distributor",
    "ingester",
    "querier",
    "query-frontend",
    "query-scheduler",
    "store-gateway",
    # we add:
    "ruler",
    "alertmanager",
}
"""The minimal set of roles that need to be allocated for the
deployment to be considered consistent (otherwise we set blocked). On top of what mimir itself lists as required,
we add alertmanager."""

RECOMMENDED_DEPLOYMENT = {
    "ingester": 3,
    "querier": 2,
    "query-scheduler": 2,
    "alertmanager": 1,
    "query-frontend": 1,
    "ruler": 1,
    "store-gateway": 1,
    "compactor": 1,
    "distributor": 1,
}
"""The set of roles that need to be allocated for the
deployment to be considered robust according to the official recommendations/guidelines."""


class MimirRolesConfig:
    """Define the configuration for Mimir roles."""

    roles: Iterable[str] = ROLES
    meta_roles: Mapping[str, Iterable[str]] = META_ROLES
    minimal_deployment: Iterable[str] = MINIMAL_DEPLOYMENT
    recommended_deployment: Dict[str, int] = RECOMMENDED_DEPLOYMENT


# The minimum number of workers per role to enable replication
REPLICATION_MIN_WORKERS = 3
# The default amount of replicas to set when there are enough workers per role;
# otherwise, replicas will be "disabled" by setting the amount to 1
DEFAULT_REPLICATION = 3


class MimirConfig:
    """Config builder for the Mimir Coordinator."""

    def __init__(
        self,
        root_data_dir: Path = Path("/data"),
        recovery_data_dir: Path = Path("/recovery-data"),
    ):
        self._root_data_dir = root_data_dir
        self._recovery_data_dir = recovery_data_dir

    def config(self, coordinator: Coordinator) -> str:
        """Generate shared config file for mimir.

        Reference: https://grafana.com/docs/mimir/latest/configure/
        """
        mimir_config: Dict[str, Any] = {
            "common": {},
            "alertmanager": self._build_alertmanager_config(coordinator.cluster),
            "alertmanager_storage": self._build_alertmanager_storage_config(),
            "compactor": self._build_compactor_config(),
            "ingester": self._build_ingester_config(coordinator.cluster),
            "ruler": self._build_ruler_config(),
            "ruler_storage": self._build_ruler_storage_config(),
            "store_gateway": self._build_store_gateway_config(coordinator.cluster),
            "blocks_storage": self._build_blocks_storage_config(),
            "memberlist": self._build_memberlist_config(coordinator.topology, coordinator.cluster),
        }

        if coordinator.s3_ready:
            mimir_config["common"]["storage"] = self._build_s3_storage_config(
                coordinator._s3_config
            )
            self._update_s3_storage_config(mimir_config["blocks_storage"], "blocks")
            self._update_s3_storage_config(mimir_config["ruler_storage"], "rules")
            self._update_s3_storage_config(mimir_config["alertmanager_storage"], "alerts")

        # todo: TLS config for memberlist
        if coordinator.nginx.are_certificates_on_disk:
            mimir_config["server"] = self._build_tls_config()

        return yaml.dump(mimir_config)

    def _build_tls_config(self) -> Dict[str, Any]:
        tls_config = {
            "cert_file": CERT_FILE,
            "key_file": KEY_FILE,
            "client_ca_file": CLIENT_CA_FILE,
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
    def _build_alertmanager_config(self, cluster: ClusterProvider) -> Dict[str, Any]:
        alertmanager_scale = len(cluster.gather_addresses_by_role().get("alertmanager", []))
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
    def _build_ingester_config(self, cluster: ClusterProvider) -> Dict[str, Any]:
        ingester_scale = len(cluster.gather_addresses_by_role().get("ingester", []))
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
    def _build_store_gateway_config(self, cluster: ClusterProvider) -> Dict[str, Any]:
        store_gateway_scale = len(cluster.gather_addresses_by_role().get("store-gateway", []))
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

    def _build_s3_storage_config(self, s3_config_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "backend": "s3",
            "s3": s3_config_data,
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
    def _build_memberlist_config(
        self, topology: JujuTopology, cluster: ClusterProvider
    ) -> Dict[str, Any]:
        top = topology.as_dict()
        return {
            "cluster_label": f"{top['model']}_{top['model_uuid']}_{top['application']}",
            "join_members": list(cluster.gather_addresses()),
        }
