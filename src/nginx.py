# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Any, Dict, List, Optional, Set

import crossplane
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from ops.pebble import Layer

logger = logging.getLogger(__name__)


NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
CA_CERT_PATH = f"{NGINX_DIR}/certs/ca.cert"

LOCATIONS_DISTRIBUTOR: List[Dict[str, Any]] = [
    {
        "directive": "location",
        "args": ["/distributor"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://distributor"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/v1/push"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://distributor"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/otlp/v1/metrics"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://distributor"],
            },
        ],
    },
]
LOCATIONS_ALERTMANAGER: List[Dict] = [
    {
        "directive": "location",
        "args": ["/alertmanager"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://alertmanager"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/multitenant_alertmanager/status"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://alertmanager"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/v1/alerts"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://alertmanager"],
            },
        ],
    },
]
LOCATIONS_RULER: List[Dict] = [
    {
        "directive": "location",
        "args": ["/prometheus/config/v1/rules"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://ruler"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/prometheus/api/v1/rules"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://ruler"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/prometheus/api/v1/alerts"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://ruler"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["=", "/ruler/ring"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://ruler"],
            },
        ],
    },
]
LOCATIONS_QUERY_FRONTEND: List[Dict] = [
    {
        "directive": "location",
        "args": ["/prometheus"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    # Buildinfo endpoint can go to any component
    {
        "directive": "location",
        "args": ["=", "/api/v1/status/buildinfo"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
]
LOCATIONS_COMPACTOR: List[Dict] = [
    # Compactor endpoint for uploading blocks
    {
        "directive": "location",
        "args": ["=", "/api/v1/upload/block/"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://compactor"],
            },
        ],
    },
]


class Nginx:
    """Helper class to manage the nginx workload."""

    config_path = NGINX_CONFIG

    def __init__(self, cluster_provider: MimirClusterProvider, server_name: str):
        self.cluster_provider = cluster_provider
        self.server_name = server_name

    def config(self, tls: bool = False) -> str:
        """Build and return the Nginx configuration."""
        log_level = "error"
        addresses_by_role = self.cluster_provider.gather_addresses_by_role()

        # build the complete configuration
        full_config = [
            {"directive": "worker_processes", "args": ["5"]},
            {"directive": "error_log", "args": ["/dev/stderr", log_level]},
            {"directive": "pid", "args": ["/tmp/nginx.pid"]},
            {"directive": "worker_rlimit_nofile", "args": ["8192"]},
            {
                "directive": "events",
                "args": [],
                "block": [{"directive": "worker_connections", "args": ["4096"]}],
            },
            {
                "directive": "http",
                "args": [],
                "block": [
                    # upstreams (load balancing)
                    *self._upstreams(addresses_by_role),
                    # temp paths
                    {"directive": "client_body_temp_path", "args": ["/tmp/client_temp"]},
                    {"directive": "proxy_temp_path", "args": ["/tmp/proxy_temp_path"]},
                    {"directive": "fastcgi_temp_path", "args": ["/tmp/fastcgi_temp"]},
                    {"directive": "uwsgi_temp_path", "args": ["/tmp/uwsgi_temp"]},
                    {"directive": "scgi_temp_path", "args": ["/tmp/scgi_temp"]},
                    # logging
                    {"directive": "default_type", "args": ["application/octet-stream"]},
                    {
                        "directive": "log_format",
                        "args": [
                            "main",
                            '$remote_addr - $remote_user [$time_local]  $status "$request" $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"',
                        ],
                    },
                    *self._log_verbose(verbose=False),
                    # mimir-related
                    {"directive": "sendfile", "args": ["on"]},
                    {"directive": "tcp_nopush", "args": ["on"]},
                    *self._resolver(custom_resolver=None),
                    # TODO: add custom http block for the user to config?
                    {
                        "directive": "map",
                        "args": ["$http_x_scope_orgid", "$ensured_x_scope_orgid"],
                        "block": [
                            {"directive": "default", "args": ["$http_x_scope_orgid"]},
                            {"directive": "", "args": ["FIXMEnoAuthTenant?"]},  # FIXME
                        ],
                    },
                    {"directive": "proxy_read_timeout", "args": ["300"]},
                    # server block
                    self._server(addresses_by_role, tls),
                ],
            },
        ]

        return crossplane.build(full_config)

    @property
    def layer(self) -> Layer:
        """Return the Pebble layer for Nginx."""
        return Layer(
            {
                "summary": "nginx layer",
                "description": "pebble config layer for Nginx",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx",
                        "command": "nginx",
                        "startup": "enabled",
                    }
                },
            }
        )

    def _log_verbose(self, verbose: bool = True) -> List[Dict[str, Any]]:
        if verbose:
            return [{"directive": "access_log", "args": ["/dev/stderr", "main"]}]
        return [
            {
                "directive": "map",
                "args": ["$status", "$loggable"],
                "block": [
                    {"directive": "~^[23]", "args": ["0"]},
                    {"directive": "default", "args": ["1"]},
                ],
            },
            {"directive": "access_log", "args": ["/dev/stderr"]},
        ]

    def _upstreams(self, addresses_by_role: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        nginx_upstreams = []
        for role, address_set in addresses_by_role.items():
            nginx_upstreams.append(
                {
                    "directive": "upstream",
                    "args": [role],
                    "block": [
                        {"directive": "server", "args": [f"{addr}:8080"]} for addr in address_set
                    ],
                }
            )

        return nginx_upstreams

    def _locations(self, addresses_by_role: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        nginx_locations = []
        roles = addresses_by_role.keys()
        if "distributor" in roles:
            nginx_locations.extend(LOCATIONS_DISTRIBUTOR)
        if "alertmanager" in roles:
            nginx_locations.extend(LOCATIONS_ALERTMANAGER)
        if "ruler" in roles:
            nginx_locations.extend(LOCATIONS_RULER)
        if "query-frontend" in roles:
            nginx_locations.extend(LOCATIONS_QUERY_FRONTEND)
        if "compactor" in roles:
            nginx_locations.extend(LOCATIONS_COMPACTOR)
        return nginx_locations

    def _resolver(self, custom_resolver: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        if custom_resolver:
            return [{"directive": "resolver", "args": [custom_resolver]}]
        return [{"directive": "resolver", "args": ["kube-dns.kube-system.svc.cluster.local."]}]

    def _basic_auth(self, enabled: bool) -> List[Optional[Dict[str, Any]]]:
        if enabled:
            return [
                {"directive": "auth_basic", "args": ['"Mimir"']},
                {
                    "directive": "auth_basic_user_file",
                    "args": ["/etc/nginx/secrets/.htpasswd"],
                },
            ]
        return []

    def _server(self, addresses_by_role: Dict[str, Set[str]], tls: bool = False) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    {"directive": "listen", "args": ["443", "ssl"]},
                    {"directive": "listen", "args": ["[::]:443", "ssl"]},
                    *self._basic_auth(auth_enabled),
                    {
                        "directive": "location",
                        "args": ["=", "/"],
                        "block": [
                            {"directive": "return", "args": ["200", "'OK'"]},
                            {"directive": "auth_basic", "args": ["off"]},
                        ],
                    },
                    {
                        "directive": "proxy_set_header",
                        "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                    },
                    # FIXME: use a suitable SERVER_NAME
                    {"directive": "server_name", "args": [self.server_name]},
                    {"directive": "ssl_certificate", "args": [CERT_PATH]},
                    {"directive": "ssl_certificate_key", "args": [KEY_PATH]},
                    {"directive": "ssl_protocols", "args": ["TLSv1", "TLSv1.1", "TLSv1.2"]},
                    {"directive": "ssl_ciphers", "args": ["HIGH:!aNULL:!MD5"]},  # pyright: ignore
                    *self._locations(addresses_by_role),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                {"directive": "listen", "args": ["8080"]},
                {"directive": "listen", "args": ["[::]:8080"]},
                *self._basic_auth(auth_enabled),
                {
                    "directive": "location",
                    "args": ["=", "/"],
                    "block": [
                        {"directive": "return", "args": ["200", "'OK'"]},
                        {"directive": "auth_basic", "args": ["off"]},
                    ],
                },
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                *self._locations(addresses_by_role),
            ],
        }
