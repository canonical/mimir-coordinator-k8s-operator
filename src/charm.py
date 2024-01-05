#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import logging
from typing import List

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from mimir_coordinator import MimirCoordinator
from nginx import Nginx

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class MimirCoordinatorK8SOperatorCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self._nginx_container = self.unit.get_container("nginx")

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)

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
    def _s3_storage(self) -> dict:  # type: ignore
        # if not self.model.relations['s3']:
        #     return {}
        return {
            "url": "foo",
            "endpoint": "bar",
            "access_key": "bar",
            "insecure": False,
            "secret_key": "x12",
        }

    @property
    def mimir_worker_relations(self) -> List[ops.Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    def _on_config_changed(self, _event: ops.ConfigChangedEvent):
        """Handle changed configuration."""
        self.publish_config()

    def publish_config(self):
        """Generate config file and publish to all workers."""
        mimir_config = self.coordinator.build_config(dict(self.config))
        self.cluster_provider.publish_configs(mimir_config)

    def _on_mimir_cluster_changed(self, _event: ops.RelationChangedEvent):
        if self.coordinator.is_coherent():
            logger.info("mimir deployment coherent: publishing configs")
            self.publish_config()
        else:
            logger.warning("this mimir deployment is incoherent")

    def _on_collect_status(self, event: ops.CollectStatusEvent):
        """Handle start event."""
        if not self.coordinator.is_coherent():
            event.add_status(
                ops.BlockedStatus(
                    "Incoherent deployment: you are " "lacking some required Mimir roles"
                )
            )

        if self.coordinator.is_recommended():
            logger.warning("This deployment is below the recommended deployment requirement.")
            event.add_status(ops.ActiveStatus("degraded"))
        else:
            event.add_status(ops.ActiveStatus())

    def _remote_write_endpoints_changed(self, _):
        # TODO Update grafana-agent config file with the new external prometheus's endpoint
        pass

    def _on_loki_relation_changed(self, _):
        # TODO Update rules relation with the new list of Loki push-api endpoints
        pass

    def _on_nginx_pebble_ready(self, _event: ops.PebbleReadyEvent) -> None:
        self._nginx_container.push(self.nginx.config_path, self.nginx.config, make_dirs=True)

        self._nginx_container.add_layer("nginx", self.nginx.layer, combine=True)
        self._nginx_container.autostart()


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
