from unittest.mock import MagicMock

import pytest

import src.nginx_config


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
            "alertmanager": ["http://some.mimir.worker.0:8080"],
            "overrides_exporter": ["http://some.mimir.worker.0:8080"],
            "flusher": ["http://some.mimir.worker.0:8080"],
            "query_frontend": ["http://some.mimir.worker.0:8080"],
            "querier": ["http://some.mimir.worker.0:8080"],
            "store_gateway": ["http://some.mimir.worker.1:8080"],
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
