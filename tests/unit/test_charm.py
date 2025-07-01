import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import pytest as pytest
from coordinated_workers.coordinator import Coordinator
import scenario
from scenario import Relation, State
import yaml

from src.mimir_config import (
    MIMIR_ROLES_CONFIG,
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
)

from helpers import get_worker_config_exemplars

from ops import testing

from charm import NGINX_PORT, NGINX_TLS_PORT

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

def test_config_exemplars(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN Loki is related over the ingress and certificates endpoints
    ingress = Relation("ingress")
    certificates = Relation("certificates")
    worker = testing.Relation("mimir-cluster")
    config = {"max_global_exemplars_per_user":0}
    state_in = State(
        relations=[
            s3,
            all_worker,
            ingress,
            certificates,
            worker,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
        config=config
    )

    # WHEN the config for max_global_exemplars_per_user is unset
    with context(context.on.relation_joined(ingress), state_in) as mgr:
        state_out = mgr.run()

        # AND Loki publishes its Nginx non-TLS port in the ingress databag
        rel = get_worker_config_exemplars(state_out.relations, "mimir-cluster")
        #rel = get_relation_data(state_out.relations, "mimir-cluster",'worker_config')
        assert rel == 0
    
    config = {"max_global_exemplars_per_user":50000}
    state_in = State(
        relations=[
            s3,
            all_worker,
            ingress,
            certificates,
            worker,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
        config=config
    )

    # WHEN the config for max_global_exemplars_per_user is unset
    with context(context.on.relation_joined(ingress), state_in) as mgr:
        state_out = mgr.run()

        # AND Loki publishes its Nginx non-TLS port in the ingress databag
        rel = get_worker_config_exemplars(state_out.relations, "mimir-cluster")
        #rel = get_relation_data(state_out.relations, "mimir-cluster",'worker_config')
        assert rel == 100000

    config = {"max_global_exemplars_per_user":500000}
    state_in = State(
        relations=[
            s3,
            all_worker,
            ingress,
            certificates,
            worker,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
        config=config
    )

    # WHEN the config for max_global_exemplars_per_user is unset
    with context(context.on.relation_joined(ingress), state_in) as mgr:
        state_out = mgr.run()

        # AND Loki publishes its Nginx non-TLS port in the ingress databag
        rel = get_worker_config_exemplars(state_out.relations, "mimir-cluster")
        #rel = get_relation_data(state_out.relations, "mimir-cluster",'worker_config')
        assert rel == 500000
