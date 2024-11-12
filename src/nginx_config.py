# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import crossplane
from cosl.coordinated_workers.coordinator import Coordinator
from cosl.coordinated_workers.nginx import CERT_PATH, KEY_PATH

logger = logging.getLogger(__name__)


def _locations_distributor(tls: bool) -> List[Dict[str, Any]]:
    return [
        {
            "directive": "location",
            "args": ["/distributor"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://distributor"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/api/v1/push"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://distributor"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/otlp/v1/metrics"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://distributor"],
                },
            ],
        },
    ]


def _locations_alertmanager(tls: bool) -> List[Dict[str, Any]]:
    return [
        {
            "directive": "location",
            "args": ["/alertmanager"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://alertmanager"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/multitenant_alertmanager/status"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://alertmanager"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/api/v1/alerts"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://alertmanager"],
                },
            ],
        },
    ]


def _locations_ruler(tls: bool) -> List[Dict[str, Any]]:
    return [
        {
            "directive": "location",
            "args": ["/prometheus/config/v1/rules"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://ruler"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/prometheus/api/v1/rules"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://ruler"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["/prometheus/api/v1/alerts"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://ruler"],
                },
            ],
        },
        {
            "directive": "location",
            "args": ["=", "/ruler/ring"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://ruler"],
                },
            ],
        },
    ]


def _locations_query_frontend(tls: bool) -> List[Dict[str, Any]]:
    return [
        {
            "directive": "location",
            "args": ["/prometheus"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://query-frontend"],
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
                    "args": [f"{'https' if tls else 'http'}://query-frontend"],
                },
            ],
        },
    ]


def _locations_compactor(tls: bool) -> List[Dict[str, Any]]:
    return [
        # Compactor endpoint for uploading blocks
        {
            "directive": "location",
            "args": ["=", "/api/v1/upload/block/"],
            "block": [
                {
                    "directive": "proxy_pass",
                    "args": [f"{'https' if tls else 'http'}://compactor"],
                },
            ],
        },
    ]


LOCATIONS_BASIC: List[Dict[str, Any]] = [
    {
        "directive": "location",
        "args": ["=", "/"],
        "block": [
            {"directive": "return", "args": ["200", "'OK'"]},
            {"directive": "auth_basic", "args": ["off"]},
        ],
    },
    {  # Location to be used by nginx-prometheus-exporter
        "directive": "location",
        "args": ["=", "/status"],
        "block": [
            {"directive": "stub_status", "args": []},
        ],
    },
]


class NginxConfig:
    """Helper class to manage the nginx workload."""

    def __init__(self):
        self.dns_IP_address = _get_dns_ip_address()

    def config(self, coordinator: Coordinator) -> str:
        """Build and return the Nginx configuration."""
        log_level = "error"
        addresses_by_role = coordinator.cluster.gather_addresses_by_role()

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
                    *self._upstreams(addresses_by_role, coordinator.nginx.options["nginx_port"]),
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
                            {"directive": "", "args": ["anonymous"]},
                        ],
                    },
                    {"directive": "proxy_read_timeout", "args": ["300"]},
                    # server block
                    self._server(
                        server_name=coordinator.hostname,
                        addresses_by_role=addresses_by_role,
                        nginx_port=coordinator.nginx.options["nginx_port"],
                        tls=coordinator.nginx.are_certificates_on_disk,
                    ),
                ],
            },
        ]

        return crossplane.build(full_config)

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

    def _upstreams(
        self, addresses_by_role: Dict[str, Set[str]], nginx_port: int
    ) -> List[Dict[str, Any]]:
        nginx_upstreams = []
        for role, address_set in addresses_by_role.items():
            nginx_upstreams.append(
                {
                    "directive": "upstream",
                    "args": [role],
                    "block": [
                        {"directive": "server", "args": [f"{addr}:{nginx_port}"]}
                        for addr in address_set
                    ],
                }
            )

        return nginx_upstreams

    def _locations(
        self, addresses_by_role: Dict[str, Set[str]], tls: bool
    ) -> List[Dict[str, Any]]:
        nginx_locations = LOCATIONS_BASIC.copy()
        roles = addresses_by_role.keys()

        if "distributor" in roles:
            nginx_locations.extend(_locations_distributor(tls))
        if "alertmanager" in roles:
            nginx_locations.extend(_locations_alertmanager(tls))
        if "ruler" in roles:
            nginx_locations.extend(_locations_ruler(tls))
        if "query-frontend" in roles:
            nginx_locations.extend(_locations_query_frontend(tls))
        if "compactor" in roles:
            nginx_locations.extend(_locations_compactor(tls))
        return nginx_locations

    def _resolver(self, custom_resolver: Optional[str] = None) -> List[Dict[str, Any]]:
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

    def _server(
        self,
        server_name: str,
        addresses_by_role: Dict[str, Set[str]],
        nginx_port: int,
        tls: bool = False,
    ) -> Dict[str, Any]:
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
                        "directive": "proxy_set_header",
                        "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                    },
                    # FIXME: use a suitable SERVER_NAME
                    {"directive": "server_name", "args": [server_name]},
                    {"directive": "ssl_certificate", "args": [CERT_PATH]},
                    {"directive": "ssl_certificate_key", "args": [KEY_PATH]},
                    {"directive": "ssl_protocols", "args": ["TLSv1", "TLSv1.1", "TLSv1.2"]},
                    {"directive": "ssl_ciphers", "args": ["HIGH:!aNULL:!MD5"]},  # pyright: ignore
                    # specify resolver to ensure that if a unit IP changes,
                    # we reroute to the new one
                    *self._resolver(custom_resolver=self.dns_IP_address),
                    *self._locations(addresses_by_role, tls),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                {"directive": "listen", "args": [f"{nginx_port}"]},
                {"directive": "listen", "args": [f"[::]:{nginx_port}"]},
                *self._basic_auth(auth_enabled),
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                *self._resolver(custom_resolver=self.dns_IP_address),
                *self._locations(addresses_by_role, tls),
            ],
        }


def _get_dns_ip_address():
    """Obtain DNS ip address from /etc/resolv.conf."""
    resolv = Path("/etc/resolv.conf").read_text()
    for line in resolv.splitlines():
        if line.startswith("nameserver"):
            # assume there's only one
            return line.split()[1].strip()
    raise RuntimeError("cannot find nameserver in /etc/resolv.conf")

