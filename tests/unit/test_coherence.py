import unittest

from mimir_coordinator import MimirCoordinator


class IsCoherent(unittest.TestCase):
    def test_empty(self):
        mc = MimirCoordinator([])
        self.assertFalse(mc.is_coherent())
