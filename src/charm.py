#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import hashlib
import logging
import socket
import subprocess
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse

import cosl.coordinated_workers.nginx
import ops
import yaml
from charms.alertmanager_k8s.v1.alertmanager_dispatch import AlertmanagerConsumer
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteProvider
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer
from cosl.coordinated_workers.coordinator import Coordinator
from ops.model import ModelError
from ops.pebble import Error as PebbleError

from mimir_config import MIMIR_ROLES_CONFIG, MimirConfig
from nginx_config import NginxConfig

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

RULES_DIR = "/etc/mimir-alerts/rules"
ALERTS_HASH_PATH = "/etc/mimir-alerts/alerts.sha256"


@trace_charm(
    tracing_endpoint="tempo_endpoint",
    server_cert="server_cert_path",
    extra_types=[
        Coordinator,
    ],
)
class MimirCoordinatorK8SOperatorCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args: Any):
        super().__init__(*args)

        self._nginx_container = self.unit.get_container("nginx")
        self._nginx_prometheus_exporter_container = self.unit.get_container(
            "nginx-prometheus-exporter"
        )
        self.ingress = IngressPerAppRequirer(
            charm=self,
            strip_prefix=True,
            scheme=lambda: urlparse(self.internal_url).scheme,
        )
        self.alertmanager = AlertmanagerConsumer(charm=self, relation_name="alertmanager")
        self.coordinator = Coordinator(
            charm=self,
            roles_config=MIMIR_ROLES_CONFIG,
            external_url=self.external_url,
            worker_metrics_port=8080,
            endpoints={
                "certificates": "certificates",
                "cluster": "mimir-cluster",
                "grafana-dashboards": "grafana-dashboards-provider",
                "logging": "logging-consumer",
                "metrics": "self-metrics-endpoint",
                "tracing": "tracing",
                "s3": "s3",
            },
            nginx_config=NginxConfig().config,
            workers_config=MimirConfig(
                alertmanager_urls=self.alertmanager.get_cluster_info()
            ).config,
        )

        if port := urlparse(self.internal_url).port:
            self.ingress.provide_ingress_requirements(port=port)

        self.grafana_source = GrafanaSourceProvider(
            self,
            source_type="prometheus",
            source_url=f"{self.external_url}/prometheus",
            extra_fields={"httpHeaderName1": "X-Scope-OrgID"},
            secure_extra_fields={"httpHeaderValue1": "anonymous"},
            refresh_event=[
                self.coordinator.cluster.on.changed,
                self.on[self.coordinator.cert_handler.certificates_relation_name].relation_changed,
                self.ingress.on.ready,
            ],
        )

        self.remote_write_provider = PrometheusRemoteWriteProvider(
            charm=self,
            server_url_func=lambda: MimirCoordinatorK8SOperatorCharm.external_url.fget(self),  # type: ignore
            endpoint_path="/api/v1/push",
        )

        with open("mimirtool", "rb") as f:
            self._nginx_container.push("/usr/bin/mimirtool", source=f, permissions=0o744)

        self._set_alerts()

        ######################################
        # === EVENT HANDLER REGISTRATION === #
        ######################################
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)
        self.framework.observe(self.ingress.on.revoked, self._on_ingress_revoked)

    ##########################
    # === EVENT HANDLERS === #
    ##########################

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent):
        """Log the obtained ingress address.

        This event refreshes the PrometheusRemoteWriteProvider address.
        """
        logger.info("Ingress for app ready on '%s'", event.url)

    def _on_ingress_revoked(self, _) -> None:
        """Log the ingress address being revoked.

        This event refreshes the PrometheusRemoteWriteProvider address.
        """
        logger.info("Ingress for app revoked")

    ######################
    # === PROPERTIES === #
    ######################

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def tempo_endpoint(self) -> Optional[str]:
        """Tempo endpoint for charm tracing."""
        if self.coordinator.tracing.is_ready():
            return self.coordinator.tracing.get_endpoint(protocol="otlp_http")
        else:
            return None

    @property
    def server_cert_path(self) -> Optional[str]:
        """Server certificate path for tls tracing."""
        return cosl.coordinated_workers.nginx.CERT_PATH

    @property
    def internal_url(self) -> str:
        """Returns workload's FQDN. Used for ingress."""
        scheme = "http"
        port = 8080
        if hasattr(self, "coordinator") and self.coordinator.nginx.are_certificates_on_disk:
            scheme = "https"
            port = 443
        return f"{scheme}://{self.hostname}:{port}"

    @property
    def external_url(self) -> str:
        """Return the external hostname to be passed to ingress via the relation."""
        try:
            if ingress_url := self.ingress.url:
                return ingress_url
        except ModelError as e:
            logger.error("Failed obtaining external url: %s.", e)
        return self.internal_url

    ###########################
    # === UTILITY METHODS === #
    ###########################

    def _pull(self, path: str) -> Optional[str]:
        """Pull file from container (without raising pebble errors).

        Returns:
            File contents if exists; None otherwise.
        """
        try:
            return cast(str, self._nginx_container.pull(path, encoding="utf-8").read())
        except (FileNotFoundError, PebbleError):
            # Drop FileNotFoundError https://github.com/canonical/operator/issues/896
            return None

    def _push(self, path: str, contents: Any):
        """Push file to container, creating subdirs as necessary."""
        self._nginx_container.push(path, contents, make_dirs=True, encoding="utf-8")

    def _push_alert_rules(self, alerts: Dict[str, Any]) -> List[str]:
        """Pushes alert rules from a rules file to the nginx container.

        Args:
            alerts: a dictionary of alert rule files, fetched from
                either a metrics consumer or a remote write provider.
        """
        paths = []
        for topology_identifier, rules_file in alerts.items():
            filename = f"juju_{topology_identifier}.rules"
            path = f"{RULES_DIR}/{filename}"

            rules = yaml.safe_dump(rules_file)

            self._push(path, rules)
            paths.append(path)
            logger.debug("Updated alert rules file %s", filename)

        return paths

    def _set_alerts(self):
        """Create alert rule files for all Mimir consumers."""

        def sha256(hashable: Any) -> str:
            """Use instead of the builtin hash() for repeatable values."""
            if isinstance(hashable, str):
                hashable = hashable.encode("utf-8")
            return hashlib.sha256(hashable).hexdigest()

        remote_write_alerts = self.remote_write_provider.alerts
        alerts_hash = sha256(str(remote_write_alerts))
        alert_rules_changed = alerts_hash != self._pull(ALERTS_HASH_PATH)

        if alert_rules_changed:
            # Update the alert rules files on disk
            self._nginx_container.remove_path(RULES_DIR, recursive=True)
            rules_file_paths: List[str] = self._push_alert_rules(remote_write_alerts)
            self._push(ALERTS_HASH_PATH, alerts_hash)
            # Push the alert rules to the Mimir cluster (persisted in s3)
            self._nginx_container.pebble.exec(
                [
                    "mimirtool",
                    "rules",
                    "sync",
                    *rules_file_paths,
                    f"--address={self.external_url}",
                    "--id=anonymous",  # multitenancy is disabled, the default tenant is 'anonymous'
                ]
            )


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
