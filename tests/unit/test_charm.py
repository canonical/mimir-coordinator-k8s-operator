from unittest.mock import MagicMock, patch

import pytest as pytest
from coordinated_workers.coordinator import Coordinator

from src.mimir_config import (
    MIMIR_ROLES_CONFIG,
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
)


@patch("coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
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


@patch("coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
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

# Test workers_config_getter method
@patch("coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
@pytest.mark.parametrize(
    "max_global_exemplars_per_user, expected_value",
    [
        (None, 0),
        (0, 0),
        (-1, 0),
        (50000, 100000),
        (100000, 100000),
        (150000, 150000),
    ],
)
def test_workers_config_getter(mock_coordinator, max_global_exemplars_per_user, expected_value):
    mc = Coordinator(None, None, "", "", 0, None, None, None)  # pyright: ignore
    
    def side_effect():
        if max_global_exemplars_per_user is None or max_global_exemplars_per_user <= 0:
            return 0
        elif 1 <= max_global_exemplars_per_user <= 100000:
            return 100000
        else:
            return max_global_exemplars_per_user
    
    mc._workers_config_getter = MagicMock(side_effect=side_effect)
    
    result = mc._workers_config_getter()
    
    assert result is expected_value
