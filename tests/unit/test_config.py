import unittest
from unittest.mock import MagicMock

from mimir_config import _S3StorageBackend
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
        expected_config = {"data_dir": "/etc/mimir/data-alertmanager"}
        self.assertEqual(alertmanager_config, expected_config)

    def test_build_alertmanager_storage_config(self):
        alertmanager_storage_config = self.coordinator._build_alertmanager_storage_config()
        expected_config = {"filesystem": {"dir": "/etc/mimir/data-alertmanager-recovery"}}
        self.assertEqual(alertmanager_storage_config, expected_config)

    def test_build_compactor_config(self):
        compactor_config = self.coordinator._build_compactor_config()
        expected_config = {"data_dir": "/etc/mimir/data-compactor"}
        self.assertEqual(compactor_config, expected_config)

    def test_build_ruler_config(self):
        ruler_config = self.coordinator._build_ruler_config()
        expected_config = {"rule_path": "/etc/mimir/data-ruler"}
        self.assertEqual(ruler_config, expected_config)

    def test_build_ruler_storage_config(self):
        ruler_storage_config = self.coordinator._build_ruler_storage_config()
        expected_config = {"filesystem": {"dir": "/etc/mimir/rules"}}
        self.assertEqual(ruler_storage_config, expected_config)

    def test_build_blocks_storage_config(self):
        blocks_storage_config = self.coordinator._build_blocks_storage_config()
        expected_config = {
            "bucket_store": {"sync_dir": "/etc/mimir/tsdb-sync"},
            "filesystem": {"dir": "/etc/mimir/blocks"},
            "tsdb": {"dir": "/etc/mimir/tsdb"},
        }
        self.assertEqual(blocks_storage_config, expected_config)

    def test_build_config_with_s3_data(self):
        s3_data = _S3StorageBackend(
            endpoint="s3.com:port",
            access_key="your_access_key",
            secret_key="your_secret_key",
            bucket="your_bucket",
            region="your_region",
        )
        mimir_config = self.coordinator.build_config(s3_data)
        self.assertIn("storage", mimir_config["common"])
        self.assertEqual(
            mimir_config["common"]["storage"], self.coordinator._build_s3_storage_config(s3_data)
        )

    def test_build_config_without_s3_data(self):
        s3_data = _S3StorageBackend()
        mimir_config = self.coordinator.build_config(s3_data)
        self.assertNotIn("storage", mimir_config["common"])

    def test_build_s3_storage_config(self):
        s3_data = _S3StorageBackend(
            endpoint="s3.com:port",
            access_key="your_access_key",
            secret_key="your_secret_key",
            bucket="your_bucket",
            region="your_region",
        )
        s3_storage_config = self.coordinator._build_s3_storage_config(s3_data)
        expected_config = {
            "backend": "s3",
            "s3": {
                "endpoint": "s3.com:port",
                "access_key_id": "your_access_key",
                "secret_access_key": "your_secret_key",
                "bucket_name": "your_bucket",
                "region": "your_region",
            },
        }
        self.assertEqual(s3_storage_config, expected_config)

    def test_update_s3_storage_config(self):
        storage_config = {"filesystem": {"dir": "/etc/mimir/blocks"}}
        self.coordinator._update_s3_storage_config(storage_config, "filesystem", "blocks")
        expected_config = {"storage_prefix": "blocks"}
        self.assertEqual(storage_config, expected_config)

    def test_ne_update_s3_storage_config(self):
        storage_config = {"storage_prefix": "blocks"}
        self.coordinator._update_s3_storage_config(storage_config, "filesystem", "blocks")
        expected_config = {"storage_prefix": "blocks"}
        self.assertEqual(storage_config, expected_config)

    def test_build_memberlist_config(self):
        self.cluster_provider.gather_addresses.return_value = ["address1", "address2"]
        memberlist_config = self.coordinator._build_memberlist_config()
        expected_config = {"join_members": ["address1", "address2"]}
        self.assertEqual(memberlist_config, expected_config)

    def test_build_tls_config(self):
        self.tls_requirer.cacert = "/path/to/cert.pem"
        self.tls_requirer.key = "/path/to/key.pem"
        self.tls_requirer.capath = "/path/to/ca.pem"
        tls_config = self.coordinator._build_tls_config()
        expected_config = {
            "tls_enabled": True,
            "tls_cert_path": "/path/to/cert.pem",
            "tls_key_path": "/path/to/key.pem",
            "tls_ca_path": "/path/to/ca.pem",
        }
        self.assertEqual(tls_config, expected_config)


if __name__ == "__main__":
    unittest.main()
