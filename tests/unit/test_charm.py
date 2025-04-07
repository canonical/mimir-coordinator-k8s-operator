from unittest.mock import MagicMock, patch

import pytest as pytest
from cosl.coordinated_workers.coordinator import Coordinator

from src.mimir_config import (
    MIMIR_ROLES_CONFIG,
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
)


@patch("cosl.coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
@pytest.mark.parametrize(
    "roles, expected",
    (
        ({"querier": 1}, False),
        ({"distributor": 1}, False),
        ({"distributor": 1, "ingester": 1}, False),
        (dict.fromkeys(MINIMAL_DEPLOYMENT, 1), True),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_coherent(mock_coordinator, roles, expected):
    mc = Coordinator(None, None, "", "", 0, None, None, None)  # pyright: ignore
    cluster_mock = MagicMock()
    cluster_mock.gather_roles = MagicMock(return_value=roles)
    mc.cluster = cluster_mock
    mc._is_coherent = None
    mc.roles_config = MIMIR_ROLES_CONFIG

    assert mc.is_coherent is expected


@patch("cosl.coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
@pytest.mark.parametrize(
    "roles, expected",
    (
        ({"query-frontend": 1}, False),
        ({"distributor": 1}, False),
        ({"distributor": 1, "ingester": 1}, False),
        (dict.fromkeys(MINIMAL_DEPLOYMENT, 1), False),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_recommended(mock_coordinator, roles, expected):
    mc = Coordinator(None, None, "", "", 0, None, None, None)  # pyright: ignore
    cluster_mock = MagicMock()
    cluster_mock.gather_roles = MagicMock(return_value=roles)
    mc.cluster = cluster_mock
    mc._is_recommended = None
    mc.roles_config = MIMIR_ROLES_CONFIG

    assert mc.is_recommended is expected
