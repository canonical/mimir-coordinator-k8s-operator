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

import coordinated_workers.nginx
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
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import charm_tracing_config
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer
from coordinated_workers.coordinator import Coordinator
from coordinated_workers.nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH, NginxConfig
from cosl.interfaces.datasource_exchange import DatasourceDict
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
        self._nginx_helper = NginxHelper(self._nginx_container)
        self.ingress = IngressPerAppRequirer(
            charm=self,
            strip_prefix=True,
            scheme=lambda: urlparse(self.internal_url).scheme,
        )
        self.alertmanager = AlertmanagerConsumer(charm=self, relation_name="alertmanager")
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
            },
            nginx_config=NginxConfig(
                server_name=self.hostname,
                upstream_configs=self._nginx_helper.upstreams(),
                server_ports_to_locations=self._nginx_helper.server_ports_to_locations(),
                enable_health_check=True,
                enable_status_page=True,
            ),
            workers_config=MimirConfig(
                alertmanager_urls=self.alertmanager.get_cluster_info(),
                max_global_exemplars_per_user=int(self.config['max_global_exemplars_per_user'])
            ).config,
            worker_ports=lambda _: tuple({8080, 9095}),
            resources_requests=self.get_resource_requests,
            container_name="charm",  # container to which resource limits will be applied
            workload_tracing_protocols=["jaeger_thrift_http"],
            catalogue_item=self._catalogue_item,
        )

        self.charm_tracing_endpoint, self.server_ca_cert = charm_tracing_config(
            self.coordinator.charm_tracing, coordinated_workers.nginx.CA_CERT_PATH
        )

        # needs to be after the Coordinator definition in order to push certificates before checking
        # if they exist
        if port := urlparse(self.internal_url).port:
            self.ingress.provide_ingress_requirements(port=port)

        self.grafana_source = GrafanaSourceProvider(
            self,
            source_type="prometheus",
            source_url=f"{self.most_external_url}/prometheus",
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

    def _reconcile(self):
        # This method contains unconditional update logic, i.e. logic that should be executed
        # regardless of the event we are processing.
        if self._nginx_container.can_connect():
            self._set_alerts()
        self._ensure_mimirtool()
        self._update_prometheus_api()
        self._update_datasource_exchange()


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(MimirCoordinatorK8SOperatorCharm)
