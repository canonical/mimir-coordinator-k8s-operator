from unittest.mock import MagicMock

import pytest as pytest
from mimir_coordinator import (
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
    MimirCoordinator,
    MimirRole,
)


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
    mock = MagicMock()
    mock.gather_roles = MagicMock(return_value=roles)
    mc = MimirCoordinator(mock)
    assert mc.is_coherent() is expected


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
    mock = MagicMock()
    mock.gather_roles = MagicMock(return_value=roles)
    mc = MimirCoordinator(mock)
    assert mc.is_recommended() is expected
