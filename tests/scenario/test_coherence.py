from itertools import chain
from unittest.mock import patch

import pytest as pytest
from charm import MimirCoordinatorK8SOperatorCharm
from mimir_coordinator import (
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
    MimirCoordinator,
    MimirRole,
)
from scenario import Context, Relation, State


def _to_endpoint_name(role: MimirRole):
    return role.value.replace("_", "-")


ALL_MIMIR_RELATION_NAMES = list(map(_to_endpoint_name, MimirRole))


@pytest.mark.parametrize(
    "roles, expected",
    (
        ({MimirRole.ruler: 1}, False),
        ({MimirRole.distributor: 1}, False),
        ({MimirRole.distributor: 1, MimirRole.ingester: 1}, False),
        (MINIMAL_DEPLOYMENT, True),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_coherent(roles, expected):
    relations = [
        Relation(endpoint=_to_endpoint_name(role), remote_unit_ids=list(range(n_units)))
        for role, n_units in roles.items()
    ] + [Relation(endpoint="send-remote-write", remote_app_name="prom")]
    state = State(relations=relations)
    ctx = Context(charm_type=MimirCoordinatorK8SOperatorCharm)

    def pre_event(charm: MimirCoordinatorK8SOperatorCharm):
        charm_relations = list(
            chain(*(charm.model.relations.get(_ep, []) for _ep in ALL_MIMIR_RELATION_NAMES))
        )
        mc = MimirCoordinator(charm_relations)
        assert mc.is_coherent() is expected

    with patch("mimir_coordinator.MimirCoordinator._relation_data_valid", lambda *_: True):
        ctx.run("update-status", state, pre_event=pre_event)


@pytest.mark.parametrize(
    "roles, expected",
    (
        ({MimirRole.ruler: 1}, False),
        ({MimirRole.distributor: 1}, False),
        ({MimirRole.distributor: 1, MimirRole.ingester: 1}, False),
        (MINIMAL_DEPLOYMENT, False),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_recommended(roles, expected):
    relations = [
        Relation(endpoint=_to_endpoint_name(role), remote_unit_ids=list(range(n_units)))
        for role, n_units in roles.items()
    ] + [Relation(endpoint="send-remote-write", remote_app_name="prom")]
    state = State(relations=relations)
    ctx = Context(charm_type=MimirCoordinatorK8SOperatorCharm)

    def pre_event(charm: MimirCoordinatorK8SOperatorCharm):
        charm_relations = list(
            chain(*(charm.model.relations.get(_ep, []) for _ep in ALL_MIMIR_RELATION_NAMES))
        )
        mc = MimirCoordinator(charm_relations)
        assert mc.is_recommended() is expected

    with patch("mimir_coordinator.MimirCoordinator._relation_data_valid", lambda *_: True):
        ctx.run("update-status", state, pre_event=pre_event)
