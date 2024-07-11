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
from typing import Any, Optional

import cosl.coordinated_workers.nginx
import ops
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteProvider
from charms.tempo_k8s.v1.charm_tracing import trace_charm
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer
from cosl.coordinated_workers.coordinator import Coordinator
from mimir_config import MimirConfig, MimirRolesConfig
from nginx_config import NginxConfig
from ops.model import ModelError

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


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

        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)
        self.ingress = IngressPerAppRequirer(charm=self, strip_prefix=True)

        self.coordinator = Coordinator(
            charm=self,
            roles_config=MimirRolesConfig(),
            s3_bucket_name="mimir",
            external_url=self.external_url,
            metrics_port="8080",
            nginx_config=NginxConfig().config,
            workers_config=MimirConfig().config,
            endpoints={
                "cluster": "mimir-cluster",
                "grafana-dashboards": "grafana-dashboards-provider",
                "metrics": "self-metrics-endpoint",
                "logging": "logging-consumer",
            },
        )

        self.remote_write_provider = PrometheusRemoteWriteProvider(
            charm=self,
            server_url_func=lambda: MimirCoordinatorK8SOperatorCharm.external_url.fget(self),  # type: ignore
            endpoint_path="/api/v1/push",
        )

        grafana_source_scheme = "https" if self.coordinator.cert_handler.available else "http"
        grafana_source_url = self.coordinator.cluster.get_address_from_role("ruler")
        self.grafana_source = GrafanaSourceProvider(
            self,
            source_type="prometheus",
            source_url=f"{grafana_source_scheme}://{grafana_source_url}:8080/prometheus",
            extra_fields={"httpHeaderName1": "X-Scope-OrgID"},
            secure_extra_fields={"httpHeaderValue1": "anonymous"},
        )

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
        if hasattr(self, "coordinator") and self.coordinator.nginx.are_certificates_on_disk:
            scheme = "https"
        return f"{scheme}://{self.hostname}:8080"

    @property
    def external_url(self) -> str:
        """Return the external hostname to be passed to ingress via the relation."""
        try:
            if ingress_url := self.ingress.url:
                return ingress_url
        except ModelError as e:
            logger.error("Failed obtaining external url: %s.", e)
        return self.internal_url


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
