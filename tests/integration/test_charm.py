#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import yaml
from helpers import (
    charm_resources,
    check_agent_data_in_mimir,
    configure_minio,
    configure_s3_integrator,
    get_unit_address,
)
from juju.application import Application
from juju.unit import Unit
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
coordinator = SimpleNamespace(name="coordinator")


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, mimir_charm: str):
    """Build the charm-under-test and deploy it together with related charms."""
    assert ops_test.model is not None  # for pyright
    await ops_test.model.deploy(mimir_charm, "mimir", resources=charm_resources())

    await ops_test.model.deploy("prometheus-k8s", "prometheus", channel="latest/edge")
    await ops_test.model.deploy("loki-k8s", "loki", channel="latest/edge")
    await ops_test.model.deploy("grafana-k8s", "grafana", channel="latest/edge")

    # Secret must be at least 8 characters: https://github.com/canonical/minio-operator/issues/137
    await ops_test.model.deploy(
        "minio",
        channel="latest/stable",
        config={"access-key": "access", "secret-key": "secretsecret"},
    )
    await ops_test.model.deploy("s3-integrator", "s3", channel="latest/stable")

    await ops_test.model.wait_for_idle(
        apps=["prometheus", "loki", "grafana", "minio"], status="active"
    )
    await ops_test.model.wait_for_idle(apps=["mimir", "s3"], status="blocked")

    await ops_test.model.integrate("mimir:self-metrics-endpoint", "prometheus")
    await ops_test.model.integrate("mimir:grafana-dashboards-provider", "grafana")
    await ops_test.model.integrate("mimir:grafana-source", "grafana")
    await ops_test.model.integrate("mimir:logging-consumer", "loki")

    # Configure Minio
    await configure_minio(ops_test)
    await configure_s3_integrator(ops_test)
    await ops_test.model.wait_for_idle(apps=["s3"], status="active")

    # Deploy Grafana agent to test the Mimir workload
    await ops_test.model.deploy("grafana-agent-k8s", "agent")
    await ops_test.model.integrate("grafana:metrics-endpoint", "agent")
    await ops_test.model.integrate("mimir:receive-remote-write", "agent")
    await ops_test.model.integrate("mimir:s3", "s3")

    await ops_test.model.wait_for_idle(apps=["mimir", "grafana", "agent", "s3"], status="active")



@retry(wait=wait_fixed(10), stop=stop_after_attempt(10))
async def test_grafana_source(ops_test: OpsTest):
    assert ops_test.model is not None
    grafana_leader: Unit = ops_test.model.applications["grafana"].units[0]  # type: ignore
    action = await grafana_leader.run_action("get-admin-password")
    action_result = await action.wait()
    admin_password = action_result.results["admin-password"]
    grafana_url = await get_unit_address(ops_test, "grafana", 0)
    response = requests.get(f"http://admin:{admin_password}@{grafana_url}:3000/api/datasources")

    assert response.status_code == 200
    assert "mimir" in response.json()[0]["name"]


async def test_metrics_endpoint(ops_test: OpsTest):
    assert ops_test.model is not None
    prometheus_url = await get_unit_address(ops_test, "prometheus", 0)
    response = requests.get(f"http://{prometheus_url}:9090/api/v1/targets")
    assert response.status_code == 200
    mimir_targets = [
        target
        for target in response.json()["data"]["activeTargets"]
        if target["discoveredLabels"]["juju_charm"] == "mimir-coordinator-k8s"
    ]
    assert mimir_targets


async def test_mimir_monolithic(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "worker",
        channel="latest/edge",
        config={"role-all": True, "role-query-frontend": True},
    )
    await ops_test.model.integrate("mimir:mimir-cluster", "worker")
    await ops_test.model.wait_for_idle(apps=["mimir", "worker", "agent", "s3"], status="active")
    await check_agent_data_in_mimir(ops_test, "mimir")
    await ops_test.model.remove_application(app_name="worker", destroy_storage=True)


async def test_mimir_multiple_workers(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy("mimir-worker-k8s", "worker-read", channel="latest/edge", config={"role-read": True})
    await ops_test.model.deploy("mimir-worker-k8s", "worker-write", channel="latest/edge", config={"role-write": True})
    await ops_test.model.deploy("mimir-worker-k8s", "worker-backend", channel="latest/edge", config={"role-backend": True, "role-alertmanager": True})

    await ops_test.model.integrate("mimir:mimir-cluster", "worker-read")
    await ops_test.model.integrate("mimir:mimir-cluster", "worker-write")
    await ops_test.model.integrate("mimir:mimir-cluster", "worker-backend")

    await ops_test.model.wait_for_idle(apps=["mimir", "worker-read", "worker-write", "worker-backend"], status="active")
    await check_agent_data_in_mimir(ops_test, "mimir")


async def test_mimir_scaled_workers(ops_test: OpsTest):
    assert ops_test.model is not None
    worker_read: Application = ops_test.model.applications["worker-read"]  # type: ignore
    worker_write: Application = ops_test.model.applications["worker-write"]  # type: ignore
    worker_backend: Application = ops_test.model.applications["worker-backend"]  # type: ignore
    await worker_read.scale(3)
    await worker_write.scale(3)
    await worker_backend.scale(3)
    await check_agent_data_in_mimir(ops_test, "mimir")


async def test_traefik(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy("traefik-k8s", "traefik", channel="latest/edge")
    await ops_test.model.integrate("mimir", "traefik")

    await ops_test.model.wait_for_idle(apps=["mimir", "traefik"], status="active")

    traefik_leader: Unit = ops_test.model.applications["traefik"].units[0]  # type: ignore
    action = await traefik_leader.run_action("show-proxied-endpoints")
    action_result = await action.wait()
    proxied_endpoints = json.loads(action_result.results["proxied-endpoints"])
    assert "mimir" in proxied_endpoints

    response = requests.get(f"{proxied_endpoints['mimir']['url']}/status")
    assert response.status_code == 200


async def test_tls(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy("self-signed-certificates", "ca")
    await ops_test.model.integrate("mimir:certificates", "ca")

    await ops_test.model.wait_for_idle(apps=["mimir", "ca"], status="active")

    mimir_url = await get_unit_address(ops_test, "mimir", 0)
    response = requests.get(f"https://{mimir_url}:443/status", verify=False)
    assert response.status_code == 200
