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
    )

    # Configure the S3 integrator
    await ops_test.model.wait_for_idle(apps=["s3"], status="blocked")
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

    # Push example payload to the `mimir-write` API
    status = await ops_test.model.get_status()

    write_app = status.applications.get('mimir-write')
    read_app = status.applications.get('mimir-read')

    assert write_app is not None, "mimir-write application not found"
    assert read_app is not None, "mimir-read application not found"

    write_address = write_app.units['mimir-write/0'].public_address
    read_address = read_app.units['mimir-read/0'].public_address

    assert write_address is not None, "Write address is None"
    assert read_address is not None, "Read address is None"

    # Prepare the payload (timeseries data)
    trace_id = "da061bde6e64e89172071263d7adb68r"
    timestamp = int(time.time() * 1000)  # Current time in milliseconds

    payload = {
        "timeseries": [
            {
                "labels": [
                    {"name": "__name__", "value": "example_metric"},
                    {"name": "job", "value": "example_job"},
                    {"name": "trace_id", "value": trace_id}
                ],
                "samples": [
                    {
                        "value": 42,
                        "timestamp": timestamp
                    }
                ],
                "exemplars": [
                    {
                        "labels": [
                            {"name": "trace_id", "value": trace_id}
                        ],
                        "value": 55,
                        "timestamp": timestamp / 1000  # Convert to seconds
                    }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
    }

    # Push the data to the Mimir Write API
    response = requests.post(write_address, json=payload, headers=headers)
    assert response.status_code == 200, f"Failed to push data to mimir-write: {response.text}"

    logger.info("Successfully pushed data to mimir-write")

    # Query the Mimir Read HTTP API to check the exemplars
    query = '{"query": "example_metric"}'
    response = requests.get(read_address, params={"query": query})
    assert response.status_code == 200, f"Failed to query exemplars: {response.text}"

    data = response.json()
    logger.info("Query response: %s", json.dumps(data, indent=2))

    # Check if the exemplar with the trace_id is present in the response
    exemplars = data.get("data", {}).get("result", [])
    found = any(
        exemplar.get("labels", {}).get("trace_id") == trace_id
        for exemplar in exemplars
    )

    assert found, f"Exemplar with trace_id {trace_id} not found in the response"
    logger.info(f"Exemplar with trace_id {trace_id} found in the response")
