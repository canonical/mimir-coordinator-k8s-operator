#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import logging
from typing import Any, Dict, List, Optional
from minio import Minio
from minio.error import S3Error

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from mimir_coordinator import MimirCoordinator
from ops.charm import CharmBase, CollectStatusEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, Relation, MaintenanceStatus

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class MimirCoordinatorK8SOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)

        self.cluster_provider = MimirClusterProvider(self)
        self.coordinator = MimirCoordinator(cluster_provider=self.cluster_provider)
        self._s3_storage_data = None
        self.framework.observe(
            self.on.mimir_cluster_relation_changed,  # pyright: ignore
            self._on_mimir_cluster_changed,
        )

        self.framework.observe(
            self.on.s3_relation_created,  # pyright: ignore
            self._on_s3_created,
        )

        self.framework.observe(
            self.on.s3_relation_changed,  # pyright: ignore
            self._on_mimir_cluster_changed,
        )

        self.framework.observe(
            self.on.s3_relation_broken,  # pyright: ignore
            self._on_s3_broken,
        )

        self.remote_write_consumer = PrometheusRemoteWriteConsumer(self)
        self.framework.observe(
            self.remote_write_consumer.on.endpoints_changed,  # pyright: ignore
            self._remote_write_endpoints_changed,
        )

        self.grafana_dashboard_provider = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboards-provider"
        )

        self.loki_consumer = LokiPushApiConsumer(self, relation_name="logging-consumer")
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_joined,  # pyright: ignore
            self._on_loki_relation_changed,
        )
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_departed,  # pyright: ignore
            self._on_loki_relation_changed,
        )

    @property
    def _s3_storage(self) -> dict:
        return self._s3_storage_data or {}

    @property
    def mimir_worker_relations(self) -> List[Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    def _on_config_changed(self, event):
        """Handle changed configuration."""
        self.publish_config()

    def publish_config(self):
        """Generate config file and publish to all workers."""
        mimir_config = self.coordinator.build_config(self._s3_storage)
        self.cluster_provider.publish_configs(mimir_config)

    def create_minio_buckets(self, conn: dict, bucket_names: list):
        """Create Minio buckets"""
        try:
            client = Minio(
                endpoint=f'{conn["service"]}.{conn["namespace"]}.svc.cluster.local:{conn["port"]}',
                access_key=conn["access_key"],
                secret_key=conn["secret_key"],
                secure=conn["secure"],
            )
            for bucket in bucket_names:
                found = client.bucket_exists(bucket)
                if not found:
                    client.make_bucket(bucket)
                else:
                    logger.info("Bucket already exists")
        except S3Error:
            logger.error("Error creating S3 buckets")

    def parse_s3_data(self, s3_data):
        data_passed_dict = dict(item.split(": ") for item in s3_data.split("\n") if item)
        data_passed_dict["secure"] = data_passed_dict.get("secure", "").lower() == "true"

        return {
            "access_key": data_passed_dict.get("access-key", ""),
            "namespace": data_passed_dict.get("namespace", ""),
            "port": data_passed_dict.get("port", ""),
            "secret_key": data_passed_dict.get("secret-key", ""),
            "secure": data_passed_dict.get("secure", ""),
            "service": data_passed_dict.get("service", ""),
        }

    def _on_mimir_cluster_changed(self, _):
        if not self.coordinator.is_coherent():
            self.unit.status = BlockedStatus(
                "Incoherent deployment: Some required Mimir roles are missing."
            )
            return
        self.unit.status = MaintenanceStatus("Configuring Mimir...")
        self.process_s3_relation()
        if not self._s3_storage_data and self.coordinator.is_scaled():
            self.unit.status = BlockedStatus("Replicated units must use S3 storage.")
            return
        self.publish_config()
        self.unit.status = ActiveStatus()


    def process_s3_relation(self):
        s3_relation = self.model.get_relation("s3")
        if not s3_relation:
            self._s3_storage_data = None
            return
        data_passed = s3_relation.data.get(s3_relation.app, {}).get("data")
        if not data_passed:
            self._s3_storage_data = None
            return
        s3_parsed_data = self.parse_s3_data(data_passed)
        self.create_minio_buckets(s3_parsed_data, ["mimir"])
        self._s3_storage_data = s3_parsed_data

    def _on_s3_created(self, _):
        s3_relation = self.model.get_relation("s3")
        s3_relation.data[self.model.app]["_supported_versions"] = "- v1"

    def _on_s3_broken(self, _):
        if not self.coordinator.is_coherent():
            self.unit.status = BlockedStatus(
                "Incoherent deployment: Some required Mimir roles are missing."
            )
            return
        self.unit.status = MaintenanceStatus("Configuring Mimir...")
        self._s3_storage_data = None
        if self.coordinator.is_scaled():
            self.unit.status = BlockedStatus("Replicated units must use S3 storage.")
            return
        self.publish_config()
        self.unit.status = ActiveStatus()

    def _on_collect_status(self, event: CollectStatusEvent):
        """Handle start event."""
        if not self.coordinator.is_coherent():
            event.add_status(
                BlockedStatus(
                    "Incoherent deployment: you are " "lacking some required Mimir roles"
                )
            )

        if self.coordinator.is_recommended():
            logger.warning("This deployment is below the recommended deployment requirement.")
            event.add_status(ActiveStatus("degraded"))
        else:
            event.add_status(ActiveStatus())

    def _remote_write_endpoints_changed(self, _):
        # TODO Update grafana-agent config file with the new external prometheus's endpoint
        pass

    def _on_loki_relation_changed(self, _):
        # TODO Update rules relation with the new list of Loki push-api endpoints
        pass


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
