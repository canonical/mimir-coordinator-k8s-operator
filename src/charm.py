#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import logging
from typing import List, Optional

from charms.data_platform_libs.v0.s3 import (
    S3Requirer,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from mimir_config import BUCKET_NAME, S3_RELATION_NAME, _S3ConfigData
from mimir_coordinator import MimirCoordinator
from nginx import Nginx
from ops.charm import CharmBase, CollectStatusEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, Relation

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class MimirCoordinatorK8SOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)

        self._nginx_container = self.unit.get_container("nginx")

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)
        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)

        self.s3_requirer = S3Requirer(self, S3_RELATION_NAME, BUCKET_NAME)
        self.cluster_provider = MimirClusterProvider(self)
        self.coordinator = MimirCoordinator(cluster_provider=self.cluster_provider)

        self.nginx = Nginx(cluster_provider=self.cluster_provider)
        self.framework.observe(
            self.on.nginx_pebble_ready,  # pyright: ignore
            self._on_nginx_pebble_ready,
        )

        self.framework.observe(
            self.on.mimir_cluster_relation_changed,  # pyright: ignore
            self._on_mimir_cluster_changed,
        )

        self.framework.observe(
            self.s3_requirer.on.credentials_changed, self._on_s3_requirer_credentials_changed
        )
        self.framework.observe(
            self.s3_requirer.on.credentials_gone, self._on_s3_requirer_credentials_gone
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
    def mimir_worker_relations(self) -> List[Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    def has_multiple_workers(self) -> bool:
        """Return True if there are multiple workers forming the Mimir cluster."""
        mimir_cluster_relations = self.model.relations.get("mimir-cluster", [])
        remote_units_count = sum(
            len(relation.units)
            for relation in mimir_cluster_relations
            if relation.app != self.model.app
        )
        return remote_units_count > 1

    def _on_config_changed(self, _):
        """Handle changed configuration."""
        s3_config_data = self._get_s3_storage_config()
        self.publish_config(s3_config_data)

    def _on_mimir_cluster_changed(self, _):
        self._process_cluster_and_s3_credentials_changes()

    def _on_s3_requirer_credentials_changed(self, _):
        self._process_cluster_and_s3_credentials_changes()

    def _process_cluster_and_s3_credentials_changes(self):
        if not self.coordinator.is_coherent():
            logger.warning("Incoherent deployment: Some required Mimir roles are missing.")
            return
        s3_config_data = self._get_s3_storage_config()
        if not s3_config_data and self.has_multiple_workers():
            logger.warning("Filesystem storage cannot be used with replicated mimir workers")
            return
        self.publish_config(s3_config_data)

    def _on_s3_requirer_credentials_gone(self, _):
        if not self.coordinator.is_coherent():
            logger.warning("Incoherent deployment: Some required Mimir roles are missing.")
            return
        if self.has_multiple_workers():
            logger.warning("Filesystem storage cannot be used with replicated mimir workers")
            return
        self.publish_config(None)

    def publish_config(self, s3_config_data: Optional[dict]):
        """Generate config file and publish to all workers."""
        mimir_config = self.coordinator.build_config(s3_config_data)
        self.cluster_provider.publish_configs(mimir_config)

    def _get_s3_storage_config(self):
        """Retrieve S3 storage configuration."""
        if not self.s3_requirer.relations:
            return None
        raw = self.s3_requirer.get_s3_connection_info()
        return _S3ConfigData.load_and_dump_to_dict(raw)

    def _on_collect_status(self, event: CollectStatusEvent):
        """Handle start event."""
        if not self.coordinator.is_coherent():
            event.add_status(
                BlockedStatus("Incoherent deployment: you are lacking some required Mimir roles")
            )
        s3_config_data = self._get_s3_storage_config()
        if not s3_config_data and self.has_multiple_workers():
            event.add_status(
                BlockedStatus(
                    "When multiple units of Mimir are deployed, you must add a valid S3 relation. S3 relation missing/invalid."
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

    def _on_nginx_pebble_ready(self, _event) -> None:
        self._nginx_container.push(self.nginx.config_path, self.nginx.config, make_dirs=True)

        self._nginx_container.add_layer("nginx", self.nginx.layer, combine=True)
        self._nginx_container.autostart()


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
