# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import patch

from charm import MimirCoordinatorK8SOperatorCharm
from ops.model import ActiveStatus
from ops.testing import Harness
from workload import WorkloadManager


class TestCharm(unittest.TestCase):
    def setUp(self):
        version_patcher = patch.object(WorkloadManager, "version", property(lambda *_: "1.2.3"))
        self.version_patch = version_patcher.start()
        self.addCleanup(version_patcher.stop)

        self.harness = Harness(MimirCoordinatorK8SOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_leader(True)
        self.harness.add_storage("data", attach=True)
        self.harness.begin_with_initial_hooks()

    def test_simple(self):
        self.assertIsInstance(self.harness.charm.unit.status, ActiveStatus)
