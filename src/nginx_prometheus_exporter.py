# Copyright 2024 Canonical
# See LICENSE file for licensing details.
"""Nginx Prometheus exporter workload."""

import logging

from nginx import NGINX_PORT
from ops.pebble import Layer

logger = logging.getLogger(__name__)

NGINX_PROMETHEUS_EXPORTER_PORT = "9113"


class NginxPrometheusExporter:
    """Helper class to manage the nginx prometheus exporter workload."""

    def __init__(self) -> None:
        pass

    @property
    def layer(self) -> Layer:
        """Return the Pebble layer for Nginx Prometheus exporter."""
        return Layer(
            {
                "summary": "nginx prometheus exporter layer",
                "description": "pebble config layer for Nginx Prometheus exporter",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx prometheus exporter",
                        "command": f"nginx-prometheus-exporter --nginx.scrape-uri=http://127.0.0.1:{NGINX_PORT}/status",
                        "startup": "enabled",
                    }
                },
            }
        )
