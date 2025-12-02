#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import asyncio
import logging

import pytest
import requests
from helpers import (
    charm_resources,
    configure_minio,
    configure_s3_integrator,
    get_grafana_datasources_from_client_localhost,
    get_prometheus_targets_from_client_localhost,
    get_traefik_proxied_endpoints,
    push_to_otelcol,
    query_exemplars,
    query_mimir_from_client_localhost,
)
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, mimir_charm: str, cos_channel):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await asyncio.gather(
        ops_test.model.deploy(
            mimir_charm,
            "mimir",
            config={"max_global_exemplars_per_user": 100000},
            num_units=3,
            resources=charm_resources(),
            trust=True,
        ),
        ops_test.model.deploy("prometheus-k8s", "prometheus", channel=cos_channel, trust=True),
        ops_test.model.deploy("loki-k8s", "loki", channel=cos_channel, trust=True),
        ops_test.model.deploy("grafana-k8s", "grafana", channel=cos_channel, trust=True),
        ops_test.model.deploy("grafana-agent-k8s", "agent", channel=cos_channel, trust=True),
        ops_test.model.deploy("traefik-k8s", "traefik", channel="latest/edge", trust=True),
        ops_test.model.deploy(
            "opentelemetry-collector-k8s", "otelcol", trust=True, channel=cos_channel
        ),
        # Deploy and configure Minio and S3
        # Secret must be at least 8 characters: https://github.com/canonical/minio-operator/issues/137
        ops_test.model.deploy(
            "minio",
            channel="ckf-1.9/stable",
            config={"access-key": "access", "secret-key": "secretsecret"},
        ),
        ops_test.model.deploy("s3-integrator", "s3", channel="latest/stable"),
    )
    await ops_test.model.wait_for_idle(apps=["minio"], status="active")
    await ops_test.model.wait_for_idle(apps=["s3"], status="blocked")
    await configure_minio(ops_test)
    await configure_s3_integrator(ops_test)

    await ops_test.model.wait_for_idle(
        apps=["prometheus", "loki", "grafana", "minio", "s3", "otelcol"], status="active"
    )
    await ops_test.model.wait_for_idle(apps=["mimir", "agent"], status="blocked")


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_workers(ops_test: OpsTest, cos_channel):
    """Deploy the Mimir workers."""
    assert ops_test.model is not None
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "worker-read",
        channel=cos_channel,
        config={"role-read": True},
        num_units=1,
        trust=True,
    )
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "worker-write",
        channel=cos_channel,
        config={"role-write": True},
        num_units=1,
        trust=True,
    )
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "worker-backend",
        channel=cos_channel,
        config={"role-backend": True},
        num_units=1,
        trust=True,
    )
    await ops_test.model.wait_for_idle(
        apps=[
            "worker-read",
            "worker-write",
            "worker-backend"
        ],
        status="blocked",
        timeout=1000,
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_integrate(ops_test: OpsTest):
    assert ops_test.model is not None
    await asyncio.gather(
        ops_test.model.integrate("mimir:s3", "s3"),
        ops_test.model.integrate("mimir:mimir-cluster", "worker-read"),
        ops_test.model.integrate("mimir:mimir-cluster", "worker-write"),
        ops_test.model.integrate("mimir:mimir-cluster", "worker-backend"),
        ops_test.model.integrate("mimir:self-metrics-endpoint", "prometheus"),
        ops_test.model.integrate("mimir:grafana-dashboards-provider", "grafana"),
        ops_test.model.integrate("mimir:grafana-source", "grafana"),
        ops_test.model.integrate("mimir:logging-consumer", "loki"),
        ops_test.model.integrate("mimir:ingress", "traefik"),
        ops_test.model.integrate("mimir:receive-remote-write", "agent"),
        ops_test.model.integrate("agent:metrics-endpoint", "grafana"),
        ops_test.model.integrate("mimir:receive-remote-write", "otelcol:send-remote-write"),
    )

    await ops_test.model.wait_for_idle(
        apps=[
            "mimir",
            "prometheus",
            "loki",
            "grafana",
            "agent",
            "minio",
            "s3",
            "worker-read",
            "worker-write",
            "worker-backend",
            "traefik",
        ],
        status="active",
        timeout=2000,
    )


async def test_scale_workers(ops_test: OpsTest):
    """Scale the Mimir workers to 2 units each."""
    assert ops_test.model is not None
    await asyncio.gather(
        ops_test.model.applications["worker-read"].scale(3),
        ops_test.model.applications["worker-write"].scale(3),
        ops_test.model.applications["worker-backend"].scale(3),
    )
    await ops_test.model.wait_for_idle(
        apps=[
            "worker-read",
            "worker-write",
            "worker-backend"
        ],
        status="active",
        timeout=1000,
        raise_on_error=False,
    )


@retry(wait=wait_fixed(10), stop=stop_after_attempt(6))
async def test_grafana_source(ops_test: OpsTest):
    """Test the grafana-source integration, by checking that Mimir appears in the Datasources."""
    assert ops_test.model is not None
    datasources = await get_grafana_datasources_from_client_localhost(ops_test)
    mimir_datasources = ["mimir" in d["name"] for d in datasources]
    assert any(mimir_datasources)
    assert len(mimir_datasources) == 1


@retry(wait=wait_fixed(10), stop=stop_after_attempt(6))
async def test_metrics_endpoint(ops_test: OpsTest):
    """Check that Mimir appears in the Prometheus Scrape Targets."""
    assert ops_test.model is not None
    targets = await get_prometheus_targets_from_client_localhost(ops_test)
    mimir_targets = [
        target
        for target in targets["activeTargets"]
        if target["discoveredLabels"]["juju_charm"] == "mimir-coordinator-k8s"
    ]
    assert mimir_targets


@retry(wait=wait_fixed(10), stop=stop_after_attempt(6))
async def test_metrics_in_mimir(ops_test: OpsTest):
    """Check that the agent metrics appear in Mimir."""
    result = await query_mimir_from_client_localhost(ops_test, query='up{juju_charm=~"grafana-agent-k8s"}')
    assert result


async def test_traefik(ops_test: OpsTest):
    """Check the ingress integration, by checking if Mimir is reachable through Traefik."""
    assert ops_test.model is not None
    proxied_endpoints = await get_traefik_proxied_endpoints(ops_test)
    assert "mimir" in proxied_endpoints

    response = requests.get(f"{proxied_endpoints['mimir']['url']}/status")
    assert response.status_code == 200


async def test_exemplars(ops_test: OpsTest):
    """Check that Mimir successfully receives and stores exemplars."""
    metric_name = "sample_metric"
    trace_id = await push_to_otelcol(ops_test, metric_name=metric_name)

    found_trace_id = await query_exemplars(
        ops_test, query_name=metric_name, coordinator_app="mimir"
    )
    assert found_trace_id == trace_id
