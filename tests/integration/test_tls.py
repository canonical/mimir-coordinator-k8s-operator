#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import logging

import pytest
import requests
from helpers import (
    charm_resources,
    get_unit_address,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_setup(ops_test: OpsTest, mimir_charm: str):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await ops_test.model.deploy(mimir_charm, "mimir", resources=charm_resources())

    await ops_test.model.deploy("self-signed-certificates", "ca")
    await ops_test.model.integrate("mimir:certificates", "ca")

    await ops_test.model.wait_for_idle(apps=["ca"], status="active")
    await ops_test.model.wait_for_idle(apps=["mimir"], status="blocked")


async def test_tls(ops_test: OpsTest):
    """Check the coordinator is correctly configuring TLS."""
    assert ops_test.model is not None
    mimir_url = await get_unit_address(ops_test, "mimir", 0)
    response = requests.get(f"https://{mimir_url}:443/status", verify=False)
    assert response.status_code == 200
