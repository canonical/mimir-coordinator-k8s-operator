#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import asyncio
import json
import logging
import time
import pytest
import requests
from pytest_operator.plugin import OpsTest
from helpers import (
    charm_resources,
    configure_minio,
    configure_s3_integrator,
    remote_write_mimir,
    query_exemplars
)
import uuid

logger = logging.getLogger(__name__)

@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, mimir_charm: str, cos_channel):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await asyncio.gather(
        ops_test.model.deploy(mimir_charm, "mimir", resources=charm_resources(), trust=True, config={"max_global_exemplars_per_user": 100000}),
        ops_test.model.deploy(
            "minio",
            channel="ckf-1.9/stable",
            config={"access-key": "access", "secret-key": "secretsecret"},
        ),
        ops_test.model.deploy("s3-integrator", "s3", channel="latest/stable"),
    )
    # Configure the S3 integrator
    await ops_test.model.wait_for_idle(apps=["minio"], status="active")
    await ops_test.model.wait_for_idle(apps=["s3"], status="blocked")
    await configure_minio(ops_test)
    await configure_s3_integrator(ops_test)

    await ops_test.model.wait_for_idle(apps=["s3"])

    await ops_test.model.wait_for_idle(
        apps=["minio", "s3"], status="active"
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
    )

    await ops_test.model.wait_for_idle(
        apps=[
            "mimir",
            "s3", 
            "mimir-read",
            "mimir-write",
            "mimir-backend"
        ],
        status="active",
    )
    
    # Prepare the payload (timeseries data)
    trace_id = str(uuid.uuid4())
    QUERYNAME = "sample_metric"
    response_code = await remote_write_mimir(ops_test, worker_app="mimir-write", traceId=trace_id, queryName=QUERYNAME)
    assert response_code == 200

    logger.info("Successfully pushed data to mimir-write")

    # Delay to ensure we are not querying the exemplars endpoint too soon
    time.sleep(10) 

    # Query the Mimir Read HTTP API to check the exemplars

    found_trace_id = await query_exemplars(ops_test, queryName=QUERYNAME, worker_app="mimir-read")
    assert found_trace_id == trace_id
