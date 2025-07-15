#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import asyncio
import logging
import time

import pytest
from helpers import (
    charm_resources,
    configure_minio,
    configure_s3_integrator,
    push_to_otelcol,
    query_exemplars,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, mimir_charm: str, cos_channel):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await asyncio.gather(
        ops_test.model.deploy(mimir_charm, "mimir", resources=charm_resources(), trust=True, config={"max_global_exemplars_per_user": 100000}), # Enable exemplars by setting the config value to a positive number
        ops_test.model.deploy(
            "minio",
            channel="ckf-1.9/stable",
            config={"access-key": "access", "secret-key": "secretsecret"},
        ),
        ops_test.model.deploy("s3-integrator", "s3", channel="latest/stable"),
        ops_test.model.deploy("opentelemetry-collector-k8s", "otel-col", trust=True, channel=cos_channel)
    )
    # Configure the S3 integrator
    await ops_test.model.wait_for_idle(apps=["minio"], status="active")
    await ops_test.model.wait_for_idle(apps=["s3"], status="blocked")
    await configure_minio(ops_test)
    await configure_s3_integrator(ops_test)

    await ops_test.model.wait_for_idle(apps=["s3"])

    await ops_test.model.wait_for_idle(
        apps=["minio", "s3", "otel-col"], status="active"
    )

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
        apps=["mimir-read", "mimir-write", "mimir-backend"], status="blocked"
    )

@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_integrate(ops_test: OpsTest):
    assert ops_test.model is not None
    await asyncio.gather(
        ops_test.model.integrate("mimir:s3", "s3"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-read"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-write"),
        ops_test.model.integrate("mimir:mimir-cluster", "mimir-backend"),
        ops_test.model.integrate("mimir:receive-remote-write", "otel-col:send-remote-write"),
    )

    await ops_test.model.wait_for_idle(
        apps=[
            "mimir",
            "s3",
            "mimir-read",
            "mimir-write",
            "mimir-backend",
        ],
        status="active",
    )

    # Prepare the payload (timeseries data)
    METRICNAME = "sample_metric"
    traceId = await push_to_otelcol(ops_test, metricName=METRICNAME)

    # Delay to ensure we are not querying the Mimir exemplars endpoint too soon
    time.sleep(10)

    # Query the Mimir Read HTTP API to check the exemplars

    found_trace_id = await query_exemplars(ops_test, queryName=METRICNAME, worker_app="mimir-read")
    assert found_trace_id == traceId
