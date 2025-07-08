#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import asyncio
import logging

import pytest
import requests
from helpers import (
    charm_resources,
    configure_s3_integrator,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, mimir_charm: str, cos_channel):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await asyncio.gather(
        ops_test.model.deploy(mimir_charm, "mimir", resources=charm_resources(), trust=True),
        ops_test.model.deploy("s3-integrator", "s3", channel="latest/stable"),
        ops_test.model.deploy("prometheus-scrape-target-k8s", "prometheus-scrape", channel=cos_channel, trust=True),
        ops_test.model.deploy("grafana-agent-k8s", "agent", channel=cos_channel, trust=True),
    )

    # Configure the S3 integrator
    await ops_test.model.wait_for_idle(apps=["s3", "prometheus-scrape", "agent"], status="blocked")
    await configure_s3_integrator(ops_test)

    await ops_test.model.wait_for_idle(apps=["s3"])

    # Wait for Mimir to be blocked
    await ops_test.model.wait_for_idle(
        apps=["mimir"], status="blocked"
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_workers(ops_test: OpsTest, cos_channel):
    """Deploy the Mimir workers."""
    assert ops_test.model is not None
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "mimir-read",
        channel=cos_channel,
        config={"role-read": True},
        trust=True,
    )
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "mimir-write",
        channel=cos_channel,
        config={"role-write": True},
        trust=True,
    )
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "mimir-backend",
        channel=cos_channel,
        config={"role-backend": True},
        trust=True,
    )
    await ops_test.model.wait_for_idle(
        apps=["mimir-read", "wmimir-write", "mimir-backend"], status="blocked"
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_integrate(ops_test: OpsTest):
    assert ops_test.model is not None
    await asyncio.gather(
        ops_test.model.integrate("mimir", "s3"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-read"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-write"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-backend"),
        ops_test.model.integrate("mimir:receive-remote-write", "agent:send-remote-write"),
        ops_test.model.integrate("prometheus-scrape: metrics-endpoint", "agent: metrics-endpoint"),
    )

    await ops_test.model.wait_for_idle(
        apps=[
            "mimir",
            "agent",
            "s3",
            "mimir-read",
            "mimir-write",
            "mimir-backend"
        ],
        status="active",
    )

