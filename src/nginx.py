# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Dict, List, Set

import crossplane
from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirClusterProvider
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class Nginx:
    """Helper class to manage the nginx workload."""

    config_path = "/etc/nginx/nginx.conf"

    def __init__(self, cluster_provider: MimirClusterProvider, *args):
        super().__init__(*args)
        self.cluster_provider = cluster_provider

    @property
    def config(self) -> str:
        """Build and return the Nginx configuration."""
        log_level = "error"
        auth_enabled = False
        addresses_by_role = self.cluster_provider.gather_addresses_by_role()

        def upstreams(addresses_by_role: Dict[str, Set[str]]) -> List[Dict]:
            nginx_upstreams = []
            for role, address_set in addresses_by_role.items():
                nginx_upstreams.append(
                    {
                        "directive": "upstream",
                        "args": [role],
                        "block": [
                            {"directive": "server", "args": [f"{addr}:8080"]}
                            for addr in address_set
                        ],
                    }
                )

            return nginx_upstreams

        def log_verbose(verbose):
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

        def resolver(custom_resolver):
            if custom_resolver:
                return [{"directive": "resolver", "args": [custom_resolver]}]
            return [{"directive": "resolver", "args": ["kube-dns.kube-system.svc.cluster.local."]}]

        def basic_auth(enabled):
            if enabled:
                return [
                    {"directive": "auth_basic", "args": ['"Mimir"']},
                    {
                        "directive": "auth_basic_user_file",
                        "args": ["/etc/nginx/secrets/.htpasswd"],
                    },
                ]
            return []

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
                    *upstreams(addresses_by_role),
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
                    *log_verbose(verbose=False),
                    # mimir-related
                    {"directive": "sendfile", "args": ["on"]},
                    {"directive": "tcp_nopush", "args": ["on"]},
                    *resolver(custom_resolver=None),
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
                    {
                        "directive": "server",
                        "args": [],
                        "block": [
                            {"directive": "listen", "args": ["8080"]},
                            {"directive": "listen", "args": ["[::]:8080"]},
                            *basic_auth(auth_enabled),
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
                            # Distributor endpoints
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
                            # Alertmanager endpoints
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
                            # Ruler endpoints
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
                            # Query frontend
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
                        ],
                    },
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
