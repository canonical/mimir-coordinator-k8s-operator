import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nginx_config import NginxConfig

sample_dns_ip = "198.18.0.0"


@contextmanager
def mock_ipv6(enable: bool):
    with patch("nginx_config.is_ipv6_enabled", MagicMock(return_value=enable)):
        yield


@pytest.fixture(scope="module")
def nginx_config():
    return NginxConfig()


@pytest.fixture(scope="module")
def coordinator():
    coord = MagicMock()
    coord.topology = MagicMock()
    coord.cluster = MagicMock()
    coord.cluster.gather_addresses_by_role = MagicMock(
        return_value={
            "alertmanager": ["http://some.mimir.worker.0:8080"],
            "overrides-exporter": ["http://some.mimir.worker.0:8080"],
            "flusher": ["http://some.mimir.worker.0:8080"],
            "query-frontend": ["http://some.mimir.worker.0:8080"],
            "query-scheduler": ["http://some.mimir.worker.0:8080"],
            "querier": ["http://some.mimir.worker.0:8080"],
            "store-gateway": ["http://some.mimir.worker.1:8080"],
            "ingester": ["http://some.mimir.worker.1:8080"],
            "distributor": ["http://some.mimir.worker.1:8080"],
            "ruler": ["http://some.mimir.worker.1:8080"],
            "compactor": ["http://some.mimir.worker.0:8080", "http://some.mimir.worker.1:8080"],
        }
    )
    coord.cluster.gather_addresses = MagicMock(
        return_value=["http://some.mimir.worker.0:8080", "http://some.mimir.worker.1:8080"]
    )
    coord.s3_ready = MagicMock(return_value=True)
    coord.nginx = MagicMock()
    coord.nginx.are_certificates_on_disk = MagicMock(return_value=True)
    coord.hostname = "localhost"  # crossplane.build does not allow unittest.mock objects
    return coord


@pytest.fixture(scope="module")
def topology():
    top = MagicMock()
    top.as_dict = MagicMock(
        return_value={
            "model": "some-model",
            "model_uuid": "some-uuid",
            "application": "mimir",
            "unit": "mimir-0",
            "charm_name": "mimir-coordinator-k8s",
        }
    )
    return top


@contextmanager
def mock_resolv_conf(contents: str):
    with tempfile.NamedTemporaryFile() as tf:
        Path(tf.name).write_text(contents)
        with patch("nginx_config.RESOLV_CONF_PATH", tf.name):
            yield


@pytest.mark.parametrize(
    "addresses_by_role",
    [
        ({"alertmanager": ["address.one"]}),
        ({"alertmanager": ["address.one", "address.two"]}),
        ({"alertmanager": ["address.one", "address.two", "address.three"]}),
    ],
)
def test_upstreams_config(nginx_config, coordinator, addresses_by_role):
    nginx_port = 8080
    upstreams_config = nginx_config._upstreams(addresses_by_role, nginx_port)
    expected_config = [
        {
            "directive": "upstream",
            "args": ["alertmanager"],
            "block": [
                {"directive": "server", "args": [f"{addr}:{nginx_port}"]}
                for addr in addresses_by_role["alertmanager"]
            ],
        }
    ]
    assert upstreams_config == expected_config


@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize("ipv6", (True, False))
def test_servers_config(ipv6, tls):
    port = 8080
    with mock_ipv6(ipv6):
        nginx = NginxConfig()
    server_config = nginx._server(
        server_name="test", addresses_by_role={}, nginx_port=port, tls=tls
    )
    ipv4_args = ["443", "ssl"] if tls else [f"{port}"]
    assert {"directive": "listen", "args": ipv4_args} in server_config["block"]
    ipv6_args = ["[::]:443", "ssl"] if tls else [f"[::]:{port}"]
    ipv6_directive = {"directive": "listen", "args": ipv6_args}
    if ipv6:
        assert ipv6_directive in server_config["block"]
    else:
        assert ipv6_directive not in server_config["block"]


def _assert_config_per_role(source_dict, address, prepared_config, tls):
    # as entire config is in a format that's hard to parse (and crossplane returns a string), we look for servers,
    # upstreams and correct proxy/grpc_pass instructions.
    # FIXME we get -> server "1.2.3.5:<MagicMock name=\'mock.nginx.options.__getitem__() ..." since we mock the coordinator
    # FIXME How can we test this? And where do we get our ports from?
    for port in source_dict.values():
        assert f"server {address}:{port};" in prepared_config
        assert f"listen {port}" in prepared_config
        assert f"listen [::]:{port}" in prepared_config
    for protocol in source_dict.keys():
        sanitised_protocol = protocol.replace("_", "-")
        assert f"upstream {sanitised_protocol}" in prepared_config

        if "grpc" in protocol:
            assert f"set $backend grpc{'s' if tls else ''}://{sanitised_protocol}"
            assert "grpc_pass $backend" in prepared_config
        else:
            assert f"set $backend http{'s' if tls else ''}://{sanitised_protocol}"
            assert "proxy_pass $backend" in prepared_config


@pytest.mark.parametrize("tls", (True, False))
def test_nginx_config_contains_upstreams_and_proxy_pass(
    context, nginx_container, coordinator, addresses, tls
):
    coordinator.nginx.are_certificates_on_disk = tls
    with mock_resolv_conf(f"nameserver {sample_dns_ip}"):
        nginx = NginxConfig()

    prepared_config = nginx.config(coordinator)
    assert f"resolver {sample_dns_ip};" in prepared_config

    for role, addresses in addresses.items():
        for address in addresses:
            if role == "distributor":
                _assert_config_per_role({"ssl": 443}, address, prepared_config, tls)
            if role == "query-frontend":
                _assert_config_per_role({"ssl": 443}, address, prepared_config, tls)


"worker_processes 5;\nerror_log /dev/stderr error;\npid /tmp/nginx.pid;\nworker_rlimit_nofile 8192;\nevents {\n    worker_connections 4096;\n}\nhttp {\n    upstream distributor {\n        server \"1.2.3.5:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream ingester {\n        server \"1.2.3.6:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream querier {\n        server \"1.2.4.7:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream query-frontend {\n        server \"1.2.5.1:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream compactor {\n        server \"1.2.6.6:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream overrides-exporter {\n        server \"1.2.8.4:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream query-scheduler {\n        server \"1.2.8.5:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream flusher {\n        server \"1.2.8.6:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream store-gateway {\n        server \"1.2.8.7:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream ruler {\n        server \"1.2.8.8:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    upstream alertmanager {\n        server \"1.2.8.9:<MagicMock name='mock.nginx.options.__getitem__()' id='127454170141008'>\";\n    }\n    client_body_temp_path /tmp/client_temp;\n    proxy_temp_path /tmp/proxy_temp_path;\n    fastcgi_temp_path /tmp/fastcgi_temp;\n    uwsgi_temp_path /tmp/uwsgi_temp;\n    scgi_temp_path /tmp/scgi_temp;\n    default_type application/octet-stream;\n    log_format main '$remote_addr - $remote_user [$time_local]  $status \"$request\" $body_bytes_sent \"$http_referer\" \"$http_user_agent\" \"$http_x_forwarded_for\"';\n    map $status $loggable {\n        ~^[23] 0;\n        default 1;\n    }\n    access_log /dev/stderr;\n    sendfile on;\n    tcp_nopush on;\n    resolver 198.18.0.0;\n    map $http_x_scope_orgid $ensured_x_scope_orgid {\n        default $http_x_scope_orgid;\n        '' anonymous;\n    }\n    proxy_read_timeout 300;\n    server {\n        listen 443 ssl;\n        listen [::]:443 ssl;\n        proxy_set_header X-Scope-OrgID $ensured_x_scope_orgid;\n        server_name localhost;\n        ssl_certificate /etc/nginx/certs/server.cert;\n        ssl_certificate_key /etc/nginx/certs/server.key;\n        ssl_protocols TLSv1 TLSv1.1 TLSv1.2;\n        ssl_ciphers HIGH:!aNULL:!MD5;\n        location = / {\n            return 200 \"'OK'\";\n            auth_basic off;\n        }\n        location = /status {\n            stub_status;\n        }\n        location /distributor {\n            proxy_pass https://distributor;\n        }\n        location /api/v1/push {\n            proxy_pass https://distributor;\n        }\n        location /otlp/v1/metrics {\n            proxy_pass https://distributor;\n        }\n        location /alertmanager {\n            proxy_pass https://alertmanager;\n        }\n        location /multitenant_alertmanager/status {\n            proxy_pass https://alertmanager;\n        }\n        location /api/v1/alerts {\n            proxy_pass https://alertmanager;\n        }\n        location /prometheus/config/v1/rules {\n            proxy_pass https://ruler;\n        }\n        location /prometheus/api/v1/rules {\n            proxy_pass https://ruler;\n        }\n        location /prometheus/api/v1/alerts {\n            proxy_pass https://ruler;\n        }\n        location = /ruler/ring {\n            proxy_pass https://ruler;\n        }\n        location /prometheus {\n            proxy_pass https://query-frontend;\n        }\n        location = /api/v1/status/buildinfo {\n            proxy_pass https://query-frontend;\n        }\n        location = /api/v1/upload/block/ {\n            proxy_pass https://compactor;\n        }\n    }\n}"
