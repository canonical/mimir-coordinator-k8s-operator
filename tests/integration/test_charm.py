#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import sh
import yaml
from helpers import charm_resources, configure_minio, configure_s3_integrator, get_unit_address
from pytest_operator.plugin import OpsTest

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


async def test_grafana_source(ops_test: OpsTest):
    assert ops_test.model is not None
    # TODO: use the Grafana API once we can deploy with anonymous access
    # grafana_leader: Unit = ops_test.model.applications["grafana"].units[0]  # type: ignore
    # action = await grafana_leader.run_action("get-admin-password")
    # action_result = await action.wait()
    # admin_password = action_result.results["admin-password"]
    # grafana_token = sh.base64(_in=f"admin:{admin_password}")
    datasources = sh.juju.ssh(
        f"--model={ops_test.model.name}",
        "--container=grafana",
        "grafana/0",
        "cat /etc/grafana/provisioning/datasources/datasources.yaml",
    )
    assert len(yaml.safe_load(datasources)["datasources"]) == 1


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


async def test_mimir_cluster(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy(
        "mimir-worker-k8s",
        "worker",
        channel="latest/edge",
        config={"role-all": True, "role-query-frontend": True},
    )
    await ops_test.model.deploy("grafana-agent-k8s", "agent")

    await ops_test.model.integrate("mimir:mimir-cluster", "worker")
    await ops_test.model.integrate("grafana:metrics-endpoint", "agent")
    await ops_test.model.integrate("mimir:receive-remote-write", "agent")
    await ops_test.model.integrate("mimir:s3", "s3")

    await ops_test.model.wait_for_idle(apps=["mimir", "worker", "agent", "s3"], status="active")

    mimir_url = await get_unit_address(ops_test, "mimir", 0)
    response = requests.get(f"http://{mimir_url}:8080/status")
    assert response.status_code == 200

    # TODO: check the data from grafana agent is in Mimir


async def test_traefik(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy("traefik-k8s", "traefik", channel="latest/edge")
    await ops_test.model.integrate("mimir", "traefik")

    # await ops_test.model.wait_for_idle(apps=["mimir", "traefik"], status="active")
    # TODO: check that ingress is working


async def test_tls(ops_test: OpsTest):
    assert ops_test.model is not None
    await ops_test.model.deploy("self-signed-certificates", "ca")
    await ops_test.model.integrate("mimir:certificates", "ca")

    await ops_test.model.wait_for_idle(apps=["mimir", "ca"], status="active")
    # TODO: check the data and some endpoints again with https
