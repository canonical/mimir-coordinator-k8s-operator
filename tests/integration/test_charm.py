#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
mc = SimpleNamespace(
    name=METADATA["name"],
    resources={
        k: METADATA["resources"][k]["upstream-source"] for k in ["nginx-image", "agent-image"]
    },
)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms."""
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".")

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(charm, resources=mc.resources, application_name=mc.name)
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)
