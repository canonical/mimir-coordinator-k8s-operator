from typing import Union
from unittest.mock import MagicMock, patch

import pytest as pytest
from coordinated_workers.coordinator import Coordinator
from helpers import get_worker_config_exemplars
from scenario import State

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

@pytest.mark.parametrize(
    "set_config, expected_exemplars",
    [
        (0, 0),               # when max_global_exemplars_per_user is 0
        (99_999, 100_000),      # when max_global_exemplars_per_user is between 1 and 100k
        (100_001, 100_001),     # when max_global_exemplars_per_user is above 100k
    ]
)
def test_config_exemplars(context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container, set_config, expected_exemplars):
    """Ensure the correct config for max_global_exemplars_per_user are sent to the worker by the coordinator."""
    # GIVEN that the exemplars are enabled in Mimir Coordinator
    config_value: Union[str, int, float, bool] = set_config
    config = {"max_global_exemplars_per_user": config_value}

    state_in = State(
        relations=[
            s3,
            all_worker,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
        config=config
    )

    # WHEN a worker joines enters a relation to a coordinator
    with context(context.on.relation_joined(all_worker), state_in) as mgr:
        state_out = mgr.run()

        # THEN the worker should have the correct exemplar limit
        config = get_worker_config_exemplars(state_out.relations, "mimir-cluster")
        assert config == expected_exemplars
