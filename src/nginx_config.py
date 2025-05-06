# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Dict, List

from cosl.coordinated_workers.nginx import (
    CA_CERT_PATH,
    CERT_PATH,
    KEY_PATH,
    NginxLocationConfig,
    NginxLocationModifier,
    NginxUpstream,
)
from ops import Container

logger = logging.getLogger(__name__)


class NginxHelper:
    """Helper class to generate the nginx configuration."""
    _upstreams = [
        "distributor",
        "compactor",
        "query-frontend",
        "ingester",
        "ruler",
        "store-gateway",
        "alertmanager",
    ]
    _locations_distributor = [
     NginxLocationConfig(path="/distributor", backend="distributor"),
     NginxLocationConfig(path="/api/v1/push", backend="distributor"),
     NginxLocationConfig(path="/otlp/v1/metrics", backend="distributor"),
    ]

    _locations_ruler = [
        NginxLocationConfig(path="/prometheus/config/v1/rules", backend="ruler"),
        NginxLocationConfig(path="/prometheus/api/v1/rules", backend="ruler"),
        NginxLocationConfig(path="/prometheus/api/v1/alerts", backend="ruler"),
        NginxLocationConfig(path="/ruler/ring", backend="ruler", modifier=NginxLocationModifier.EXACT),
    ]


    _locations_alertmanager = [
        NginxLocationConfig(path="/alertmanager", backend="alertmanager"),
        NginxLocationConfig(path="/multitenant_alertmanager/status", backend="alertmanager"),
        NginxLocationConfig(path="/api/v1/alerts", backend="alertmanager"),
    ]


    _locations_query_frontend = [
        NginxLocationConfig(path="/prometheus", backend="query-frontend"),
        # Buildinfo endpoint can go to any component
        NginxLocationConfig(path="/api/v1/status/buildinfo", backend="query-frontend", modifier=NginxLocationModifier.EXACT),
    ]

    _locations_compactor = [
        NginxLocationConfig(path="/api/v1/upload/block/", backend="compactor", modifier=NginxLocationModifier.EXACT),
    ]

    _port = 8080
    _tls_port = 443

    def __init__(self, container: Container):
        self._container = container

    def upstreams(self) -> List[NginxUpstream]:
        """Generate the list of Nginx upstream metadata configurations."""
        return [NginxUpstream(upstream, self._port, upstream) for upstream in self._upstreams]

    def server_ports_to_locations(self) -> Dict[int, List[NginxLocationConfig]]:
        """Generate a mapping from server ports to a list of Nginx location configurations."""
        return {
            self._tls_port if self._tls_available else self._port:
                self._locations_distributor +
                self._locations_ruler +
                self._locations_alertmanager +
                self._locations_query_frontend +
                self._locations_compactor
        }

    @property
    def _tls_available(self) -> bool:
        return (
                self._container.can_connect()
                and self._container.exists(CERT_PATH)
                and self._container.exists(KEY_PATH)
                and self._container.exists(CA_CERT_PATH)
            )
