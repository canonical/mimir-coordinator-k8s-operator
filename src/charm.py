#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import json
import logging
import socket
import subprocess
from pathlib import Path
from typing import Any, List, Optional

import ops
from charms.data_platform_libs.v0.s3 import (
    S3Requirer,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from charms.observability_libs.v1.cert_handler import CertHandler
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from charms.tempo_k8s.v1.charm_tracing import trace_charm
from charms.tempo_k8s.v1.tracing import TracingEndpointRequirer
from mimir_config import BUCKET_NAME, S3_RELATION_NAME, _S3ConfigData
from mimir_coordinator import MimirCoordinator
from nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH, Nginx
from ops.charm import CollectStatusEvent
from pydantic import ValidationError

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


@trace_charm(
    tracing_endpoint="tempo_endpoint",
    server_cert="server_cert_path",
    extra_types=[
        S3Requirer,
        MimirClusterProvider,
        MimirCoordinator,
        Nginx,
    ],
)
class MimirCoordinatorK8SOperatorCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args: Any):
        super().__init__(*args)

        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)

        self._nginx_container = self.unit.get_container("nginx")
        self.server_cert = CertHandler(
            charm=self,
            key="mimir-server-cert",
            sans=[self.hostname],
        )
        self.s3_requirer = S3Requirer(self, S3_RELATION_NAME, BUCKET_NAME)
        self.cluster_provider = MimirClusterProvider(self)
        self.coordinator = MimirCoordinator(
            cluster_provider=self.cluster_provider,
            tls_requirer=self.server_cert,
        )
        self.nginx = Nginx(cluster_provider=self.cluster_provider, server_name=self.hostname)
        self.tracing = TracingEndpointRequirer(self)
        self.remote_write_consumer = PrometheusRemoteWriteConsumer(self)
        self.grafana_dashboard_provider = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboards-provider"
        )
        self.loki_consumer = LokiPushApiConsumer(self, relation_name="logging-consumer")

        ######################################
        # === EVENT HANDLER REGISTRATION === #
        ######################################
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)
        self.framework.observe(self.on.nginx_pebble_ready, self._on_nginx_pebble_ready)
        self.framework.observe(
            self.on.mimir_cluster_relation_changed, self._on_mimir_cluster_changed
        )
        self.framework.observe(
            self.on.mimir_cluster_relation_departed, self._on_mimir_cluster_departed
        )
        self.framework.observe(
            self.s3_requirer.on.credentials_changed, self._on_s3_requirer_credentials_changed
        )
        self.framework.observe(
            self.s3_requirer.on.credentials_gone, self._on_s3_requirer_credentials_gone
        )
        self.framework.observe(
            self.remote_write_consumer.on.endpoints_changed,
            self._on_remote_write_endpoints_changed,
        )
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_joined, self._on_loki_relation_changed
        )
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_departed, self._on_loki_relation_changed
        )
        self.framework.observe(self.server_cert.on.cert_changed, self._on_server_cert_changed)

    ##########################
    # === EVENT HANDLERS === #
    ##########################

    def _on_config_changed(self, _: ops.ConfigChangedEvent):
        """Handle changed configuration."""
        s3_config_data = self._get_s3_storage_config()
        self.publish_config(s3_config_data)

    def _on_server_cert_changed(self, _):
        self._update_cert()
        self._ensure_pebble_layer()
        self.publish_config(tls=self._is_cert_available)

    def _on_mimir_cluster_changed(self, _):
        self._process_cluster_and_s3_credentials_changes()
        self.publish_config(tls=self._is_cert_available)

    def _on_mimir_cluster_departed(self, _):
        self._process_cluster_and_s3_credentials_changes()
        self.publish_config()

    def _on_s3_requirer_credentials_changed(self, _):
        self._process_cluster_and_s3_credentials_changes()

    def _on_s3_requirer_credentials_gone(self, _):
        if not self.coordinator.is_coherent():
            logger.warning("Incoherent deployment: Some required Mimir roles are missing.")
            return
        if self.has_multiple_workers():
            logger.warning("Filesystem storage cannot be used with replicated mimir workers")
            return
        self.publish_config(None)

    def _on_collect_status(self, event: CollectStatusEvent):
        """Handle start event."""
        if not self.coordinator.is_coherent():
            event.add_status(
                ops.BlockedStatus(
                    "Incoherent deployment: you are lacking some required Mimir roles"
                )
            )
        s3_config_data = self._get_s3_storage_config()
        if not s3_config_data and self.has_multiple_workers():
            event.add_status(
                ops.BlockedStatus(
                    "When multiple units of Mimir are deployed, you must add a valid S3 relation. S3 relation missing/invalid."
                )
            )

        if self.coordinator.is_recommended():
            logger.warning("This deployment is below the recommended deployment requirement.")
            event.add_status(ops.ActiveStatus("degraded"))
        else:
            event.add_status(ops.ActiveStatus())

    def _on_remote_write_endpoints_changed(self, _):
        # TODO Update grafana-agent config file with the new external prometheus's endpoint
        pass

    def _on_loki_relation_changed(self, _):
        # TODO Update rules relation with the new list of Loki push-api endpoints
        pass

    def _on_nginx_pebble_ready(self, _) -> None:
        self._ensure_pebble_layer()

    ######################
    # === PROPERTIES === #
    ######################

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _is_cert_available(self) -> bool:
        return (
            self.server_cert.enabled
            and (self.server_cert.server_cert is not None)
            and (self.server_cert.private_key is not None)
            and (self.server_cert.ca_cert is not None)
        )

    @property
    def mimir_worker_relations(self) -> List[ops.Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    @property
    def tempo_endpoint(self) -> Optional[str]:
        """Tempo endpoint for charm tracing."""
        if self.tracing.is_ready():
            return self.tracing.otlp_http_endpoint()
        else:
            return None

    @property
    def server_cert_path(self) -> Optional[str]:
        """Server certificate path for tls tracing."""
        return CERT_PATH

    ###########################
    # === UTILITY METHODS === #
    ###########################

    def has_multiple_workers(self) -> bool:
        """Return True if there are multiple workers forming the Mimir cluster."""
        mimir_cluster_relations = self.model.relations.get("mimir-cluster", [])
        remote_units_count = sum(
            len(relation.units)
            for relation in mimir_cluster_relations
            if relation.app != self.model.app
        )
        return remote_units_count > 1

    def publish_config(self, s3_config_data: Optional[_S3ConfigData] = None, tls: bool = False):
        """Generate config file and publish to all workers."""
        mimir_config = self.coordinator.build_config(
            s3_config_data=s3_config_data, tls_enabled=tls
        )
        self.cluster_provider.publish_configs(mimir_config)

        if tls:
            self.publish_grant_secrets()

    def publish_grant_secrets(self) -> None:
        """Publish and Grant secrets to the mimir-cluster relation."""
        secrets = {
            "private_key_secret_id": self.server_cert.private_key_secret_id,
            "ca_server_cert_secret_id": self.server_cert.ca_server_cert_secret_id,
        }

        relations = self.model.relations["mimir-cluster"]
        for relation in relations:
            relation.data[self.model.app]["secrets"] = json.dumps(secrets)
            logger.debug("Secrets published")

            for secret_id in secrets.values():
                secret = self.model.get_secret(id=secret_id)
                secret.grant(relation)

    def _process_cluster_and_s3_credentials_changes(self):
        if not self.coordinator.is_coherent():
            logger.warning("Incoherent deployment: Some required Mimir roles are missing.")
            return
        s3_config_data = self._get_s3_storage_config()
        if not s3_config_data and self.has_multiple_workers():
            logger.warning("Filesystem storage cannot be used with replicated mimir workers")
            return
        self.publish_config(s3_config_data)

    def _get_s3_storage_config(self):
        """Retrieve S3 storage configuration."""
        if not self.s3_requirer.relations:
            return None
        raw = self.s3_requirer.get_s3_connection_info()
        try:
            return _S3ConfigData(**raw)
        except ValidationError:
            msg = f"failed to validate s3 config data: {raw}"
            logger.error(msg, exc_info=True)
            return None

    def _ensure_pebble_layer(self) -> None:
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
                self.server_cert.server_cert,  # pyright: ignore
                make_dirs=True,
            )
            self._nginx_container.push(
                KEY_PATH,
                self.server_cert.private_key,  # pyright: ignore
                make_dirs=True,
            )
            # Save the CA among the trusted CAs and trust it
            self._nginx_container.push(
                ca_cert_path,
                self.server_cert.ca_cert,  # pyright: ignore
                make_dirs=True,
            )
            self._nginx_container.push(
                CA_CERT_PATH,
                self.server_cert.ca_cert,  # pyright: ignore
                make_dirs=True,
            )
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
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
