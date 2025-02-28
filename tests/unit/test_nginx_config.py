from unittest.mock import MagicMock

import pytest

import src.nginx_config
from mimir_config import MimirConfig


@pytest.fixture(scope="module")
def nginx_config():
    return src.nginx_config.NginxConfig()


@pytest.fixture(scope="module")
def coordinator():
    coord = MagicMock()
    coord.topology = MagicMock()
    coord.cluster = MagicMock()
    coord.cluster.gather_addresses_by_role = MagicMock(
        return_value={
            "alertmanager": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}"],
            "overrides-exporter": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}"],
            "flusher": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}"],
            "query-frontend": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}"],
            "querier": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}"],
            "store-gateway": [f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"],
            "ingester": [f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"],
            "distributor": [f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"],
            "ruler": [f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"],
            "compactor": [f"http://some.mimir.worker.0:{MimirConfig.http_api_port}", f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"],
        }
    )
    coord.cluster.gather_addresses = MagicMock(
        return_value=[f"http://some.mimir.worker.0:{MimirConfig.http_api_port}", f"http://some.mimir.worker.1:{MimirConfig.http_api_port}"]
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
    mimir_port = MimirConfig.http_api_port
    upstreams_config = nginx_config._upstreams(addresses_by_role, mimir_port)
    expected_config = [
        {
            "directive": "upstream",
            "args": ["alertmanager"],
            "block": [
                {"directive": "server", "args": [f"{addr}:{mimir_port}"]}
                for addr in addresses_by_role["alertmanager"]
            ],
        }
    ]
    assert upstreams_config == expected_config
