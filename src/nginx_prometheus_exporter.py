# Copyright 2024 Canonical
# See LICENSE file for licensing details.
"""Nginx Prometheus exporter workload."""

import logging

from nginx import NGINX_PORT
from ops import CharmBase
from ops.pebble import Layer

logger = logging.getLogger(__name__)

NGINX_PROMETHEUS_EXPORTER_PORT = "9113"


class NginxPrometheusExporter:
    """Helper class to manage the nginx prometheus exporter workload."""

    def __init__(self, charm: CharmBase) -> None:
        self._charm = charm

    @property
    def layer(self) -> Layer:
        """Return the Pebble layer for Nginx Prometheus exporter."""
        scheme = "https" if self._charm._is_cert_available else "http"
        return Layer(
            {
                "summary": "nginx prometheus exporter layer",
                "description": "pebble config layer for Nginx Prometheus exporter",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx prometheus exporter",
                        "command": f"nginx-prometheus-exporter --no-nginx.ssl-verify --nginx.scrape-uri={scheme}://127.0.0.1:{NGINX_PORT}/status",
                        "startup": "enabled",
                    }
                },
            }
        )
