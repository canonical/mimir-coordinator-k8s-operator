#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import asyncio
import json
import logging
import time
import pytest
import snappy
import requests
from remote_pb2 import WriteRequest
from pytest_operator.plugin import OpsTest
from helpers import (
    charm_resources,
    configure_minio,
    configure_s3_integrator,
)


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

    # Push example payload to the `mimir-write` API
    status = await ops_test.model.get_status()

    write_app = status.applications.get('mimir-write')
    read_app = status.applications.get('mimir-read')

    assert write_app is not None, "mimir-write application not found"
    assert read_app is not None, "mimir-read application not found"

    write_unit = write_app.units.get('mimir-write/0')
    read_unit = read_app.units.get('mimir-read/0')

    if write_unit:
        write_address = write_unit.address
    else:
        raise ValueError("mimir-write/0 unit not found")

    if read_unit:
        read_address = read_unit.address
    else:
        raise ValueError("mimir-read/0 unit not found")

    assert write_address is not None, "Write address is None"
    assert read_address is not None, "Read address is None"

    read_endpoint = f"http://{read_address}:8080/prometheus/api/v1/query_exemplars"
    write_endpoint = f"http://{write_address}:8080/api/v1/push"
    
    # Prepare the payload (timeseries data)
    trace_id = "da061bde6e64e89172071263d7adb68r"
    timestamp = int(time.time() * 1000)  # Current time in milliseconds

    # Create the WriteRequest Protobuf object
    remote_write = WriteRequest()

    # Add timeseries data
    series = remote_write.timeseries.add()

    # Add labels (metric name, job name, etc.)
    series.labels.add(name="__name__", value="example_metric")
    series.labels.add(name="job", value="example_job")

    ts = int(time.time() * 1000)  # Convert to milliseconds

    # Add the trace_id as a label (no timestamp here)
    trace_id = "da061bde6e64e89172071263d7adb68r"
    series.labels.add(name="trace_id", value=trace_id)

    # Add sample with value and timestamp
    sample = series.samples.add()
    sample.timestamp = ts
    sample.value = 42  # Sample value

    # Create exemplar with timestamp
    exemplar = series.exemplars.add()
    exemplar.value = 50000  # Exemplar value
    exemplar.timestamp = ts 

    # Add the trace_id label to the exemplar (as part of the exemplar)
    exemplar.labels.add(name="trace_id", value=trace_id)

    # Serialize the Protobuf payload to binary format
    serialized_payload = remote_write.SerializeToString()

    # Compress the Protobuf payload with Snappy
    compressed_payload = snappy.compress(serialized_payload)

    # Set headers for the request
    headers = {
        "Content-Type": "application/x-protobuf",  # Specify Protobuf content type
        "Content-Encoding": "snappy",              # Indicate Snappy compression
    }

    # Push the data to the Mimir Write API
    response = requests.post(write_endpoint, data=compressed_payload, headers=headers)
    assert response.status_code == 200, f"Failed to push data to mimir-write: {response.text}"

    logger.info("Successfully pushed data to mimir-write")

    # Delay to ensure we are not querying the exemplars endpoint too soon
    time.sleep(10) 

    # Query the Mimir Read HTTP API to check the exemplars
    params = {
        'query': 'example_metric'
    }

    response = requests.get(read_endpoint, params=params)
    assert response.status_code == 200, f"Failed to query exemplars: {response.text}"

    response_data = response.json()

    logger.info("Query response: %s", json.dumps(response_data, indent=2))

    # Check if the exemplar with the trace_id is present in the response
    exemplars = response_data.get("data", [])[0].get("exemplars", [])

    # Find the `trace_id` from the first exemplar's labels
    trace_id = None
    if exemplars:
        trace_id = exemplars[0].get("labels", {}).get("trace_id")

    found = any(
        exemplar.get("labels", {}).get("trace_id") == trace_id
        for exemplar in exemplars
    )

    assert found, f"Exemplar with trace_id {trace_id} not found in the response"
    logger.info(f"Exemplar with trace_id {trace_id} found in the response")
