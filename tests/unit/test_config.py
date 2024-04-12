import unittest
from unittest.mock import MagicMock

from deepdiff import DeepDiff
from mimir_config import _S3ConfigData
from mimir_coordinator import MimirCoordinator


class TestMimirConfig(unittest.TestCase):
    def setUp(self):
        self.cluster_provider = MagicMock()
        self.tls_requirer = MagicMock()
        self.coordinator = MimirCoordinator(
            cluster_provider=self.cluster_provider,
            tls_requirer=self.tls_requirer,
        )

    def test_build_alertmanager_config(self):
        alertmanager_config = self.coordinator._build_alertmanager_config()
        expected_config = {
            "data_dir": "/data/data-alertmanager",
            "sharding_ring": {"replication_factor": 1},
        }
        self.assertEqual(alertmanager_config, expected_config)

    def test_build_alertmanager_storage_config(self):
        alertmanager_storage_config = self.coordinator._build_alertmanager_storage_config()
        expected_config = {"filesystem": {"dir": "/recovery-data/data-alertmanager"}}
        self.assertEqual(alertmanager_storage_config, expected_config)

    def test_build_compactor_config(self):
        compactor_config = self.coordinator._build_compactor_config()
        expected_config = {"data_dir": "/data/data-compactor"}
        self.assertEqual(compactor_config, expected_config)

    def test_build_ruler_config(self):
        ruler_config = self.coordinator._build_ruler_config()
        expected_config = {"rule_path": "/data/data-ruler"}
        self.assertEqual(ruler_config, expected_config)

    def test_build_ruler_storage_config(self):
        ruler_storage_config = self.coordinator._build_ruler_storage_config()
        expected_config = {"filesystem": {"dir": "/data/rules"}}
        self.assertEqual(ruler_storage_config, expected_config)

    def test_build_blocks_storage_config(self):
        blocks_storage_config = self.coordinator._build_blocks_storage_config()
        expected_config = {
            "bucket_store": {"sync_dir": "/data/tsdb-sync"},
            "filesystem": {"dir": "/data/blocks"},
            "tsdb": {"dir": "/data/tsdb"},
        }
        self.assertEqual(blocks_storage_config, expected_config)

    def test_build_config_with_s3_data(self):
        raw_s3_config_data = {
            "endpoint": "s3.com:port",
            "access-key": "your_access_key",
            "secret-key": "your_secret_key",
            "bucket": "your_bucket",
            "region": "your_region",
        }
        s3_config_data = _S3ConfigData(**raw_s3_config_data)
        mimir_config = self.coordinator.build_config(s3_config_data)
        self.assertEqual(
            mimir_config["common"]["storage"],
            self.coordinator._build_s3_storage_config(s3_config_data),
        )

    def test_build_config_without_s3_data(self):
        s3_config_data = None
        mimir_config = self.coordinator.build_config(s3_config_data)
        self.assertNotIn("storage", mimir_config["common"])

    def test_build_s3_storage_config(self):
        raw_s3_config_data = {
            "endpoint": "https://s3.com:port",
            "access-key": "your_access_key",
            "secret-key": "your_secret_key",
            "bucket": "your_bucket",
            "region": "your_region",
        }
        s3_config_data = _S3ConfigData(**raw_s3_config_data)
        s3_storage_config = self.coordinator._build_s3_storage_config(s3_config_data)
        expected_config_https = {
            "backend": "s3",
            "s3": {
                "endpoint": "s3.com:port",
                "access_key_id": "your_access_key",
                "secret_access_key": "your_secret_key",
                "bucket_name": "your_bucket",
                "region": "your_region",
                "insecure": "false",
            },
        }
        self.assertEqual(DeepDiff(s3_storage_config, expected_config_https), {})

        expected_config_http = {
            "backend": "s3",
            "s3": {
                "endpoint": "s3.com:port",
                "access_key_id": "your_access_key",
                "secret_access_key": "your_secret_key",
                "bucket_name": "your_bucket",
                "region": "your_region",
                "insecure": "true",
            },
        }

        raw_s3_config_data["endpoint"] = "http://s3.com:port"
        s3_config_data = _S3ConfigData(**raw_s3_config_data)
        s3_storage_config = self.coordinator._build_s3_storage_config(s3_config_data)
        self.assertEqual(DeepDiff(s3_storage_config, expected_config_http), {})

    def test_update_s3_storage_config(self):
        storage_config = {"filesystem": {"dir": "/data/blocks"}}
        self.coordinator._update_s3_storage_config(storage_config, "blocks")
        expected_config = {"storage_prefix": "blocks"}
        self.assertEqual(storage_config, expected_config)

    def test_ne_update_s3_storage_config(self):
        storage_config = {"storage_prefix": "blocks"}
        self.coordinator._update_s3_storage_config(storage_config, "blocks")
        expected_config = {"storage_prefix": "blocks"}
        self.assertEqual(storage_config, expected_config)

    def test_build_memberlist_config(self):
        self.cluster_provider.gather_addresses.return_value = ["address1", "address2"]
        memberlist_config = self.coordinator._build_memberlist_config()
        expected_config = {"cluster_label": "something", "join_members": ["address1", "address2"]}
        self.assertIn("cluster_label", expected_config)
        memberlist_config["cluster_label"] = "something"
        self.assertEqual(memberlist_config, expected_config)

    def test_build_tls_config(self):
        tls_config = self.coordinator._build_tls_config()
        expected_config = {
            "http_tls_config": {
                "cert_file": "/etc/mimir/server.cert",
                "key_file": "/etc/mimir/private.key",
                "client_ca_file": "/etc/mimir/ca.cert",
                "client_auth_type": "RequestClientCert",
            },
            "grpc_tls_config": {
                "cert_file": "/etc/mimir/server.cert",
                "key_file": "/etc/mimir/private.key",
                "client_ca_file": "/etc/mimir/ca.cert",
                "client_auth_type": "RequestClientCert",
            },
        }
        self.assertEqual(tls_config, expected_config)


if __name__ == "__main__":
    unittest.main()
