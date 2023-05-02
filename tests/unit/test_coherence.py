import unittest

from charm import is_coherent


class IsCoherent(unittest.TestCase):
    def test_is_coherent(self):
        self.assertEqual(is_coherent([]), False)
        self.assertEqual(
            is_coherent(
                [
                    "query-frontend",
                    "querier",
                    "store-gateway",
                    "distributor",
                    "ingester",
                    "ruler",
                    "alertmanager",
                    "compactor",
                ]
            ),
            True,
        )
