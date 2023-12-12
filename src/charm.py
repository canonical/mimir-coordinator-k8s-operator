#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import logging
import socket
import subprocess
from pathlib import Path
from typing import List

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from charms.observability_libs.v0.cert_handler import CertHandler
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from mimir_coordinator import MimirCoordinator
from nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH, Nginx
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

        self.server_cert = CertHandler(
            self,
            key="mimir-server-cert",
            peer_relation_name="replicas",
            extra_sans_dns=[self.hostname],
        )
        self.cluster_provider = MimirClusterProvider(self)
        self.coordinator = MimirCoordinator(
            cluster_provider=self.cluster_provider,
            tls_requirer=self.server_cert,
        )

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
        self.framework.observe(
            self.server_cert.on.cert_changed,  # pyright: ignore
            self._on_server_cert_changed,
        )

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _is_cert_available(self) -> bool:
        return (
            self.server_cert.enabled
            and (self.server_cert.cert is not None)
            and (self.server_cert.key is not None)
            and (self.server_cert.ca is not None)
        )

    @property
    def _s3_storage(self) -> dict:
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
    def mimir_worker_relations(self) -> List[Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    def _on_config_changed(self, event):
        """Handle changed configuration."""
        self.publish_config()

    def _on_server_cert_changed(self, _):
        self._update_cert()
        self._on_nginx_pebble_ready(_)
        self.publish_config(tls=self._is_cert_available)

    def publish_config(self, tls: bool = False):
        """Generate config file and publish to all workers."""
        mimir_config = self.coordinator.build_config(dict(self.config), tls=tls)
        logger.warning(mimir_config)
        self.cluster_provider.publish_configs(mimir_config)

    def _on_mimir_cluster_changed(self, _):
        if self.coordinator.is_coherent():
            logger.info("mimir deployment coherent: publishing configs")
            self.publish_config()
        else:
            logger.warning("this mimir deployment is incoherent")

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

    def _on_nginx_pebble_ready(self, _) -> None:
        self._nginx_container.push(
            self.nginx.config_path, self.nginx.config(tls=self._is_cert_available), make_dirs=True
        )
        self._nginx_container.add_layer("nginx", self.nginx.layer, combine=True)
        self._nginx_container.autostart()

    def _update_cert(self):
        if not self._nginx_container.can_connect():
            return

        ca_cert_path = Path("/usr/local/share/ca-certificates/ca.crt")

        if self._is_cert_available:
            # Save the workload certificates
            self._nginx_container.push(
                CERT_PATH,
                self.server_cert.cert,  # pyright: ignore
                make_dirs=True,
            )
            self._nginx_container.push(
                KEY_PATH,
                self.server_cert.key,  # pyright: ignore
                make_dirs=True,
            )
            # Save the CA among the trusted CAs and trust it
            self._nginx_container.push(
                ca_cert_path,
                self.server_cert.ca,  # pyright: ignore
                make_dirs=True,
            )
            # FIXME with the update-ca-certificates machinery prometheus shouldn't need
            #  CA_CERT_PATH.
            self._nginx_container.push(
                CA_CERT_PATH,
                self.server_cert.ca,  # pyright: ignore
                make_dirs=True,
            )

            # Repeat for the charm container. We need it there for prometheus client requests.
            ca_cert_path.parent.mkdir(exist_ok=True, parents=True)
            ca_cert_path.write_text(self.server_cert.ca)  # pyright: ignore
        else:
            self._nginx_container.remove_path(CERT_PATH, recursive=True)
            self._nginx_container.remove_path(KEY_PATH, recursive=True)
            self._nginx_container.remove_path(ca_cert_path, recursive=True)
            self._nginx_container.remove_path(
                CA_CERT_PATH, recursive=True
            )  # TODO: remove (see FIXME ^)
            # Repeat for the charm container.
            ca_cert_path.unlink(missing_ok=True)

        # TODO: We need to install update-ca-certificates in Nginx Rock
        # self._nginx_container.exec(["update-ca-certificates", "--fresh"]).wait()
        subprocess.run(["update-ca-certificates", "--fresh"])


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
