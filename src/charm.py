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
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse

import ops
import yaml
from charms.alertmanager_k8s.v1.alertmanager_dispatch import AlertmanagerConsumer
from charms.catalogue_k8s.v1.catalogue import CatalogueItem
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.mimir_coordinator_k8s.v0.prometheus_api import (
    DEFAULT_RELATION_NAME as PROMETHEUS_API_RELATION_NAME,
)
from charms.mimir_coordinator_k8s.v0.prometheus_api import PrometheusApiProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer
from coordinated_workers.coordinator import Coordinator
from coordinated_workers.nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH, NginxConfig
from coordinated_workers.telemetry_correlation import TelemetryCorrelation
from cosl import JujuTopology
from cosl.interfaces.datasource_exchange import DatasourceDict
from cosl.time_validation import is_valid_timespec
from ops import ActiveStatus, BlockedStatus
from ops.model import ModelError
from ops.pebble import Error as PebbleError

from mimir_config import MIMIR_ROLES_CONFIG, MimirConfig
from nginx_config import NginxHelper

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

RULES_DIR = "/etc/mimir-alerts/rules"
ALERTS_HASH_PATH = "/etc/mimir-alerts/alerts.sha256"
NGINX_PORT = NginxHelper._port
NGINX_TLS_PORT = NginxHelper._tls_port


class MimirCoordinatorK8SOperatorCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args: Any):
        super().__init__(*args)

        self._nginx_container = self.unit.get_container("nginx")
        self._nginx_prometheus_exporter_container = self.unit.get_container(
            "nginx-prometheus-exporter"
        )
        self._nginx_helper = NginxHelper(self._nginx_container)
        self.ingress = IngressPerAppRequirer(
            charm=self,
            strip_prefix=True,
            scheme=lambda: urlparse(self.internal_url).scheme,
        )
        self.alertmanager = AlertmanagerConsumer(charm=self, relation_name="alertmanager")
        self.retention_period = str(self.config['metrics_retention_period'])
        self.coordinator = Coordinator(
            charm=self,
            roles_config=MIMIR_ROLES_CONFIG,
            external_url=self.most_external_url,
            worker_metrics_port=8080,
            endpoints={  # pyright: ignore
                "certificates": "certificates",
                "cluster": "mimir-cluster",
                "grafana-dashboards": "grafana-dashboards-provider",
                "logging": "logging-consumer",
                "metrics": "self-metrics-endpoint",
                "charm-tracing": "charm-tracing",
                "workload-tracing": "workload-tracing",
                "s3": "s3",
                "send-datasource": "send-datasource",
                "receive-datasource": None,
                "catalogue": "catalogue",
                "service-mesh": None,
                "service-mesh-provide-cmr-mesh": None,
                "service-mesh-require-cmr-mesh": None,
            },
            nginx_config=NginxConfig(
                server_name=self.hostname,
                upstream_configs=self._nginx_helper.upstreams(),
                server_ports_to_locations=self._nginx_helper.server_ports_to_locations(),
                enable_health_check=True,
                enable_status_page=True,
            ),
            workers_config=MimirConfig(
                topology=JujuTopology.from_charm(self),
                alertmanager_urls=self.alertmanager.get_cluster_info(),
                max_global_exemplars_per_user=int(self.config["max_global_exemplars_per_user"]),
                metrics_retention_period=self.retention_period if is_valid_timespec(self.retention_period) else None
            ).config,
            worker_ports=lambda _: tuple({8080, 9095}),
            resources_requests=self.get_resource_requests,
            container_name="nginx",  # container to which resource limits will be applied
            workload_tracing_protocols=["jaeger_thrift_http"],
            catalogue_item=self._catalogue_item,
            peer_relation="mimir-peers",
        )

        # needs to be after the Coordinator definition in order to push certificates before checking
        # if they exist
        if port := urlparse(self.internal_url).port:
            self.ingress.provide_ingress_requirements(port=port)

        self._telemetry_correlation = TelemetryCorrelation(
            app_name=self.app.name,
            grafana_source_relations=self.model.relations["grafana-source"],
            datasource_exchange_relations=self.model.relations["send-datasource"],
        )

        self.grafana_source = GrafanaSourceProvider(
            self,
            source_type="prometheus",
            source_url=f"{self.most_external_url}/prometheus",
            extra_fields=self._build_grafana_source_extra_fields(),
            secure_extra_fields={"httpHeaderValue1": "anonymous"},
            is_ingress_per_app=self.ingress.is_ready(),
        )

        self.remote_write_provider = PrometheusRemoteWriteProvider(
            charm=self,
            server_url_func=lambda: MimirCoordinatorK8SOperatorCharm.most_external_url.fget(self),  # type: ignore
            endpoint_path="/api/v1/push",
        )

        # refuse to handle any other event as we can't possibly know what to do.
        if not self.coordinator.can_handle_events:
            # logging is handled by the Coordinator object
            return

        # do this regardless of what event we are processing
        self._reconcile()

        ######################################
        # === EVENT HANDLER REGISTRATION === #
        ######################################
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)
        self.framework.observe(self.ingress.on.revoked, self._on_ingress_revoked)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

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
        port = NGINX_PORT
        if hasattr(self, "coordinator") and self.coordinator.nginx.are_certificates_on_disk:
            scheme = "https"
            port = NGINX_TLS_PORT
        return f"{scheme}://{self.hostname}:{port}"

    @property
    def external_url(self) -> Optional[str]:
        """Return the external hostname received from an ingress relation, if it exists."""
        try:
            if ingress_url := self.ingress.url:
                return ingress_url
        except ModelError as e:
            logger.error("Failed obtaining external url: %s.", e)
        return None

    @property
    def most_external_url(self) -> str:
        """Return the most external url known about by this charm.

        This will return the first of:
        - the external URL, if the ingress is configured and ready
        - the internal URL
        """
        external_url = self.external_url
        if external_url:
            return external_url

        return self.internal_url

    @property
    def _catalogue_item(self) -> CatalogueItem:
        api_endpoints = {
            "Prometheus rules": "/prometheus/api/v1/rules",
            "Active alerts": "/promtheus/api/v1/alerts",
            "Query": "/prometheus/api/v1/query",
            "Push": "/api/v1/push",
            "OTLP metrics": "/otlp/v1/metrics",
        }
        """A catalogue application entry for this Mimir instance."""
        return CatalogueItem(
            name="Mimir",
            icon="ruler",
            url="",
            description=(
                "Mimir provides horizontally scalable, highly available, "
                "multi-tenant, long-term storage for Prometheus. "
                "(no user interface available)"
            ),
            api_docs="https://grafana.com/docs/mimir/latest/references/http-api/",
            api_endpoints={key: f"{self.external_url}{path}" for key, path in api_endpoints.items()},
        )

    # TODO: make this a static method in the Nginx class
    @property
    def are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        nginx_container = self.unit.get_container("nginx")

        return (
            nginx_container.can_connect()
            and nginx_container.exists(CERT_PATH)
            and nginx_container.exists(KEY_PATH)
            and nginx_container.exists(CA_CERT_PATH)
        )

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

    def _ensure_mimirtool(self):
        """Copy the `mimirtool` binary to the `nginx` container if it's not there already.

        Assumes the nginx container can connect.
        """
        if self._nginx_container.can_connect():
            if self._nginx_container.exists("/usr/bin/mimirtool"):
                return
            with open("mimirtool", "rb") as f:
                self._nginx_container.push("/usr/bin/mimirtool", source=f, permissions=0o744)

    def _set_alerts(self):
        """Create alert rule files for all Mimir consumers.

        Assumes the nginx container can connect.
        """
        # Get mimirtool if this is the first execution
        self._ensure_mimirtool()

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
            mimirtool_output = self._nginx_container.pebble.exec(
                [
                    "mimirtool",
                    "rules",
                    "sync",
                    *rules_file_paths,
                    f"--address={self.most_external_url}",
                    "--id=anonymous",  # multitenancy is disabled, the default tenant is 'anonymous'
                ],
                encoding="utf-8",
            )
            if mimirtool_output.stdout:
                logger.info(f"mimirtool: {mimirtool_output.stdout.read().strip()}")
            if mimirtool_output.stderr:
                logger.error(f"mimirtool (err): {mimirtool_output.stderr.read().strip()}")

    def _update_prometheus_api(self) -> None:
        """Update all applications related to us via the prometheus-api relation."""
        if not self.unit.is_leader():
            return

        prometheus_api = PrometheusApiProvider(
            relation_mapping=self.model.relations,
            app=self.app,
            relation_name=PROMETHEUS_API_RELATION_NAME,
        )
        prometheus_api.publish(
            direct_url=f"{self.internal_url}/prometheus",
            ingress_url=f"{self.external_url}/prometheus" if self.external_url else None,
        )

    def _update_datasource_exchange(self) -> None:
        """Update the grafana-datasource-exchange relations."""
        if not self.unit.is_leader():
            return

        # we might have multiple grafana-source relations, this method collects them all and returns a mapping from
        # the `grafana_uid` to the contents of the `datasource_uids` field
        # for simplicity, we assume that we're sending the same data to different grafanas.
        # read more in https://discourse.charmhub.io/t/tempo-ha-docs-correlating-traces-metrics-logs/16116
        grafana_uids_to_units_to_uids = self.grafana_source.get_source_uids()
        raw_datasources: List[DatasourceDict] = []

        for grafana_uid, ds_uids in grafana_uids_to_units_to_uids.items():
            for _, ds_uid in ds_uids.items():
                raw_datasources.append(
                    {"type": "prometheus", "uid": ds_uid, "grafana_uid": grafana_uid}
                )
        self.coordinator.datasource_exchange.publish(datasources=raw_datasources)

    def get_resource_requests(self, _) -> Dict[str, str]:
        """Returns a dictionary for the "requests" portion of the resources requirements."""
        return {"cpu": "50m", "memory": "100Mi"}

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent):
        event.add_status(ActiveStatus())
        if not is_valid_timespec(self.retention_period):
            logger.info(f"Suspending data deletion due to invalid option set in config: {self.retention_period}. To resume data deletion, please reset value to a valid option.")
            event.add_status(BlockedStatus(f"Invalid config option (see debug-log): retention_period={self.retention_period}"))

    def _reconcile(self):
        # This method contains unconditional update logic, i.e. logic that should be executed
        # regardless of the event we are processing.
        if self._nginx_container.can_connect():
            self._set_alerts()
        self._ensure_mimirtool()
        self._update_prometheus_api()
        self._update_datasource_exchange()
        self.grafana_source.update_source(source_url=f"{self.most_external_url}/prometheus")


    def _build_grafana_source_extra_fields(self) -> Dict[str, Any]:
        """Extra fields needed for the grafana-source relation, like data correlation config."""
        metrics_to_traces_config = self._build_metrics_to_traces_config()

        return {
            "httpHeaderName1": "X-Scope-OrgID",
            **metrics_to_traces_config,
        }

    def _build_metrics_to_traces_config(self) -> Dict[str, Any]:
        # TODO: move this into the grafana_source library
        # reference: https://grafana.com/docs/grafana/latest/datasources/prometheus/configure/#provision-the-prometheus-data-source
        # this feature is only available when exemplar storage is enabled
        if int(self.config["max_global_exemplars_per_user"]) <= 0:
            logger.info("metrics-to-traces feature is disabled because exemplar storage is disabled.")
            return {}

        if datasource := self._telemetry_correlation.find_correlated_datasource(
            datasource_type="tempo",
            correlation_feature="metrics-to-traces",
        ):
            return {
                "exemplarTraceIdDestinations": [{
                        "datasourceUid": datasource.uid,
                        "name": "traceID",
                    }
                ]
            }
        return {}

if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
