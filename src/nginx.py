# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging

import crossplane
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class Nginx:
    """Helper class to manage the nginx workload."""

    config_path = "/etc/nginx/nginx.conf"

    def __init__(self, *args):
        super().__init__(*args)

    @property
    def config(self) -> str:
        """Build and return the Nginx configuration."""
        log_level = "error"
        auth_enabled = False
        addresses = {
            "FIXME": "unit.app-endpoints.model.svc.cluster.local",
            "distributor": "worker.worker-endpoints.cos.svc.cluster.local",
            "alertmanager": "worker.worker-endpoints.cos.svc.cluster.local",
            "ruler": "worker.worker-endpoints.cos.svc.cluster.local",
            "query_frontend": "worker.worker-endpoints.cos.svc.cluster.local",
            "compactor": "worker.worker-endpoints.cos.svc.cluster.local",
        }  # FIXME example, get it from somewhere

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
                return {"directive": "resolver", "args": [custom_resolver]}
            return {}  # return the CoreDNS cluster local address

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
                            'main \'$remote_addr - $remote_user [$time_local]  $status "$request" $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for";'
                        ],
                    },
                    *log_verbose(verbose=False),
                    # mimir-related
                    {"directive": "sendfile", "args": ["on"]},
                    {"directive": "tcp_nopush", "args": ["on"]},
                    resolver(custom_resolver=None),  # empty for now, check if it's necessary
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
                                        "directive": "set",
                                        "args": ["$distributor", addresses["distributor"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$distributor:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/api/v1/push"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$distributor", addresses["distributor"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$distributor:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/otlp/v1/metrics"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$distributor", addresses["distributor"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$distributor:8080$request_uri"],
                                    },
                                ],
                            },
                            # Alertmanager endpoints
                            {
                                "directive": "location",
                                "args": ["/alertmanager"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$alertmanager", addresses["alertmanager"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$alertmanager:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/multitenant_alertmanager/status"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$alertmanager", addresses["alertmanager"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$alertmanager:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/api/v1/alerts"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$alertmanager", addresses["alertmanager"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$alertmanager:8080$request_uri"],
                                    },
                                ],
                            },
                            # Ruler endpoints
                            {
                                "directive": "location",
                                "args": ["/prometheus/config/v1/rules"],
                                "block": [
                                    {"directive": "set", "args": ["$ruler", addresses["rules"]]},
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$ruler:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/prometheus/api/v1/rules"],
                                "block": [
                                    {"directive": "set", "args": ["$ruler", addresses["ruler"]]},
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$ruler:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["/prometheus/api/v1/alerts"],
                                "block": [
                                    {"directive": "set", "args": ["$ruler", addresses["ruler"]]},
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$ruler:8080$request_uri"],
                                    },
                                ],
                            },
                            {
                                "directive": "location",
                                "args": ["=", "/ruler/ring"],
                                "block": [
                                    {"directive": "set", "args": ["$ruler", addresses["ruler"]]},
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$ruler:8080$request_uri"],
                                    },
                                ],
                            },
                            # Query frontend
                            {
                                "directive": "location",
                                "args": ["/prometheus"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$query_frontend", addresses["query_frontend"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$query_frontend:8080$request_uri"],
                                    },
                                ],
                            },
                            # Buildinfo endpoint can go to any component
                            {
                                "directive": "location",
                                "args": ["=", "/api/v1/status/buildinfo"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$query_frontend", addresses["query_frontend"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$query_frontend:8080$request_uri"],
                                    },
                                ],
                            },
                            # Compactor endpoint for uploading blocks
                            {
                                "directive": "location",
                                "args": ["=", "/api/v1/upload/block/"],
                                "block": [
                                    {
                                        "directive": "set",
                                        "args": ["$compactor", addresses["compactor"]],
                                    },
                                    {
                                        "directive": "proxy_pass",
                                        "args": ["http://$compactor:8080$request_uri"],
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
