from contextlib import contextmanager
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from coordinated_workers.nginx import NginxConfig

from nginx_config import NginxHelper


@contextmanager
def mock_ipv6(enable: bool):
    with patch("coordinated_workers.nginx.is_ipv6_enabled", MagicMock(return_value=enable)):
        yield


@pytest.fixture(scope="module")
def nginx_config():
    def _nginx_config(tls=False, ipv6=True):
        with mock_ipv6(ipv6):
            with patch.object(NginxHelper, "_tls_available", new=PropertyMock(return_value=tls)):
                nginx_helper = NginxHelper(MagicMock())
                return NginxConfig(server_name="localhost",
                                    upstream_configs=nginx_helper.upstreams(),
                                    server_ports_to_locations=nginx_helper.server_ports_to_locations())
    return _nginx_config


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
    upstreams_config = nginx_config(tls=False).get_config(addresses_by_role, False)
    for role, addrs in addresses_by_role.items():
        assert f"upstream {role}" in upstreams_config
        for addr in addrs:
            assert f"server {addr}:{nginx_port}" in upstreams_config


@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize("ipv6", (True, False))
def test_servers_config(ipv6, tls, nginx_config):
    port = 8080
    server_config = nginx_config(tls=tls, ipv6=ipv6).get_config(
        {"distributor": ["address.one"]}, tls
    )
    ipv4_args = "443 ssl" if tls else f"{port}"
    assert f"listen {ipv4_args}" in  server_config
    ipv6_args = "[::]:443 ssl" if tls else f"[::]:{port}"
    if ipv6:
        assert f"listen {ipv6_args}" in server_config
    else:
        assert f"listen {ipv6_args}" not in server_config
