#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import glob
import logging
import os
import shutil
import socket
from typing import Any
from urllib.parse import urlparse

import cosl.coordinated_workers.nginx
import ops
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteProvider
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import charm_tracing_config
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer
from cosl.coordinated_workers.coordinator import Coordinator
from ops.model import ModelError

from mimir_config import MIMIR_ROLES_CONFIG, MimirConfig
from nginx_config import NginxConfig

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

NGINX_ORIGINAL_ALERT_RULES_PATH = "./src/prometheus_alert_rules/nginx"
WORKER_ORIGINAL_ALERT_RULES_PATH = "./src/prometheus_alert_rules/mimir_workers"
CONSOLIDATED_ALERT_RULES_PATH = "./src/prometheus_alert_rules/consolidated_rules"


@trace_charm(
    tracing_endpoint="charm_tracing_endpoint",
    server_cert="server_ca_cert",
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
                "charm-tracing": "charm-tracing",
                "workload-tracing": "workload-tracing",
                "s3": "s3",
            },
            nginx_config=NginxConfig().config,
            workers_config=MimirConfig().config,
            workload_tracing_protocols=["jaeger_thrift_http"],
        )

        self.charm_tracing_endpoint, self.server_ca_cert = charm_tracing_config(
            self.coordi  grpc_tls_config: &id001nator.charm_tracing, cosl.coordinated_workers.nginx.CA_CERT_PATH
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
        self._consolidate_nginx_rules()

        self.remote_write_provider = PrometheusRemoteWriteProvider(
            charm=self,
            server_url_func=lambda: MimirCoordinatorK8SOperatorCharm.external_url.fget(self),  # type: ignore
            endpoint_path="/api/v1/push",
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

    # FIXME: Move the alert_rules handling to Coordinator
    def _consolidate_nginx_rules(self):
        os.makedirs(CONSOLIDATED_ALERT_RULES_PATH, exist_ok=True)
        os.makedirs(CONSOLIDATED_ALERT_RULES_PATH, exist_ok=True)
        for filename in glob.glob(os.path.join(NGINX_ORIGINAL_ALERT_RULES_PATH, "*.*")):
            shutil.copy(filename, f"{CONSOLIDATED_ALERT_RULES_PATH}/")


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
