# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest

import ops.testing
from charm import MimirCoordinatorK8SOperatorCharm
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    def setUp(self):
        # Enable more accurate simulation of container networking.
        # For more information, see https://juju.is/docs/sdk/testing#heading--simulate-can-connect
        self.harness = Harness(MimirCoordinatorK8SOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin_with_initial_hooks()

    def test_dummy(self):
        self.assertEqual(0, 0)
