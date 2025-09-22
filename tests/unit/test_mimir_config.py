import unittest
from unittest.mock import MagicMock

import pytest
from deepdiff import DeepDiff

from src.mimir_config import MimirConfig


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

@pytest.fixture(scope="module")
def mimir_config(topology):
    return MimirConfig(topology=topology, alertmanager_urls={"http://some.am.0:9093", "http://some.am.1:9093"})


@pytest.fixture(scope="module")
def coordinator():
    coord = MagicMock()
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





@pytest.mark.parametrize(
    "addresses_by_role, replication",
    [
        ({"alertmanager": ["address.one"]}, 1),
        ({"alertmanager": ["address.one", "address.two"]}, 1),
        ({"alertmanager": ["address.one", "address.two", "address.three"]}, 3),
    ],
)
def test_build_alertmanager_config(mimir_config, coordinator, addresses_by_role, replication):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses_by_role
    alertmanager_config = mimir_config._build_alertmanager_config(coordinator.cluster)
    expected_config = {
        "data_dir": "/data/data-alertmanager",
        "sharding_ring": {"replication_factor": replication},
    }
    assert alertmanager_config == expected_config

@pytest.mark.parametrize(
    "max_global_exemplars_per_user, expected_value",
    [
        (None, 0),             # When value is None, it should return 0
        (0, 0),                # When value is 0 or negative, it should return 0
        (-1, 0),
        (50_000, 100_000),       # When value is between 1 and 100000, it should return 100000
        (99_999, 100_000),
        (100_000, 100_000),      # When value is exactly 100000, it should return 100000
        (150_000, 150_000),      # When value is greater than 100000, it should remain unchanged
        (100_001, 100_001)
    ],
)
def test_max_global_exemplars_per_user_logic(mimir_config, max_global_exemplars_per_user, expected_value):
    # Set the _max_global_exemplars_per_user to the value being tested
    mimir_config._max_global_exemplars_per_user = max_global_exemplars_per_user

    # Build the limits config
    limits_config = mimir_config._build_limits_config()

    # Assert that the value for max_global_exemplars_per_user matches the expected value
    assert limits_config["max_global_exemplars_per_user"] == expected_value

def test_build_alertmanager_storage_config(mimir_config):
    alertmanager_storage_config = mimir_config._build_alertmanager_storage_config()
    expected_config = {"filesystem": {"dir": "/recovery-data/data-alertmanager"}}
    assert DeepDiff(alertmanager_storage_config, expected_config) == {}


def test_build_compactor_config(mimir_config):
    compactor_config = mimir_config._build_compactor_config()
    expected_config = {"data_dir": "/data/data-compactor"}
    assert compactor_config == expected_config


@pytest.mark.parametrize(
    "addresses_by_role, replication",
    [
        ({"ingester": ["address.one"]}, 1),
        ({"ingester": ["address.one", "address.two"]}, 1),
        ({"ingester": ["address.one", "address.two", "address.three"]}, 3),
    ],
)
def test_build_ingester_config(mimir_config, coordinator, addresses_by_role, replication):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses_by_role
    ingester_config = mimir_config._build_ingester_config(coordinator.cluster)
    expected_config = {"ring": {"replication_factor": replication}}
    assert ingester_config == expected_config


def test_build_ruler_config(mimir_config):
    ruler_config = mimir_config._build_ruler_config()
    expected_config = {
        "rule_path": "/data/data-ruler",
        "alertmanager_url": "http://some.am.0:9093,http://some.am.1:9093",
    }
    assert ruler_config == expected_config


@pytest.mark.parametrize(
    "addresses_by_role, replication",
    [
        ({"store-gateway": ["address.one"]}, 1),
        ({"store-gateway": ["address.one", "address.two"]}, 1),
        ({"store-gateway": ["address.one", "address.two", "address.three"]}, 3),
    ],
)
def test_build_store_gateway_config(mimir_config, coordinator, addresses_by_role, replication):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses_by_role
    store_gateway_config = mimir_config._build_store_gateway_config(coordinator.cluster)
    expected_config = {"sharding_ring": {"replication_factor": replication}}
    assert store_gateway_config == expected_config


def test_build_ruler_storage_config(mimir_config):
    ruler_storage_config = mimir_config._build_ruler_storage_config()
    expected_config = {"filesystem": {"dir": "/data/rules"}}
    assert ruler_storage_config == expected_config


def test_build_blocks_storage_config(mimir_config):
    blocks_storage_config = mimir_config._build_blocks_storage_config()
    expected_config = {
        "bucket_store": {"sync_dir": "/data/tsdb-sync"},
        "filesystem": {"dir": "/data/blocks"},
        "tsdb": {"dir": "/data/tsdb"},
    }
    assert blocks_storage_config == expected_config


def test_build_s3_storage_config(mimir_config, coordinator):
    # HTTP endpoint
    s3_data_http = {
        "endpoint": "s3.com:port",
        "access_key_id": "your_access_key",
        "secret_access_key": "your_secret_key",
        "bucket_name": "your_bucket",
        "region": "your_region",
        "insecure": "true",
    }
    s3_storage_config_http = mimir_config._build_s3_storage_config(s3_data_http.copy())
    expected_config_http = {"backend": "s3", "s3": s3_data_http}
    assert s3_storage_config_http == expected_config_http

    # HTTPS endpoint
    s3_data_https = {
        "endpoint": "https://s3.com:port",
        "access-key": "your_access_key",
        "secret-key": "your_secret_key",
        "bucket": "your_bucket",
        "region": "your_region",
        "insecure": "false",
    }
    s3_storage_config_https = mimir_config._build_s3_storage_config(s3_data_https.copy())
    expected_config_https = {"backend": "s3", "s3": s3_data_https}
    assert s3_storage_config_https == expected_config_https


def test_update_s3_storage_config(mimir_config):
    storage_config = {"filesystem": {"dir": "/data/blocks"}}
    mimir_config._update_s3_storage_config(storage_config, "blocks")
    expected_config = {"storage_prefix": "blocks"}
    assert storage_config == expected_config


def test_empty_update_s3_storage_config(mimir_config):
    storage_config = {"storage_prefix": "blocks"}
    mimir_config._update_s3_storage_config(storage_config, "blocks")
    expected_config = {"storage_prefix": "blocks"}
    assert storage_config == expected_config


def test_build_memberlist_config(mimir_config, coordinator):
    memberlist_config = mimir_config._build_memberlist_config(coordinator.cluster)
    expected_config = {
        "cluster_label": "some-model_some-uuid_mimir",
        "join_members": ["http://some.mimir.worker.0:8080", "http://some.mimir.worker.1:8080"],
    }
    assert memberlist_config == expected_config


def test_build_tls_config(mimir_config):
    tls_config = mimir_config._build_tls_config()
    expected_config = {
        "http_tls_config": {
            "cert_file": "/etc/worker/server.cert",
            "key_file": "/etc/worker/private.key",
            "client_ca_file": "/etc/worker/ca.cert",
            "client_auth_type": "RequestClientCert",
        },
        # FIXME: investigate adding grpc_tls_config: https://github.com/canonical/mimir-coordinator-k8s-operator/issues/141
    }
    assert tls_config == expected_config

@pytest.mark.parametrize(
    "retention_period_config, expected_value",
    [
        ("1m", "1m"),
        ("1w", "1w"),
        ("0", 0)
    ],
)
def test_retention_period_logic(mimir_config, retention_period_config, expected_value):
    # Set the compactor_blocks_retention_period to the value being tested
    mimir_config._blocks_retention_period = retention_period_config

    # Build the limits config
    limits_config = mimir_config._build_limits_config()

    # Assert that the value for compactor_blocks_retention_period matches the expected value
    assert limits_config["compactor_blocks_retention_period"] == expected_value

if __name__ == "__main__":
    unittest.main()
