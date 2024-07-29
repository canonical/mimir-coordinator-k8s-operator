#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

# pyright: reportAttributeAccessIssue=false

from juju.unit import Unit
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

    await ops_test.model.deploy("minio", channel="latest/edge", config={"access-key": "access", "secret-key": "secret"})
    await ops_test.model.deploy("s3-integrator", "s3", channel="latest/edge")

    await ops_test.model.wait_for_idle(apps=["prometheus", "loki", "grafana", "minio"], status="active", timeout=300)
    await ops_test.model.wait_for_idle(apps=["mimir", "s3"], status="blocked", timeout=300)

    await ops_test.model.integrate("mimir:self-metrics-endpoint", "prometheus")
    await ops_test.model.integrate("mimir:grafana-dashboards-provider", "grafana")
    await ops_test.model.integrate("mimir:grafana-source", "grafana")
    await ops_test.model.integrate("mimir:logging-consumer", "loki")

    # Configure Minio
    await configure_minio(ops_test)
    await configure_s3_integrator(ops_test)

    await ops_test.model.wait_for_idle(apps=["minio", "s3"], status="active", timeout=300)
    # sh.juju.deploy("s3-integrator", "s3", channel="latest/edge", model=model)
    # sh.juju.config("s3", "endpoint=localhost:8333", model=model)
    # sh.juju.config("s3", "bucket=mimir", model=model)

    # sh.juju.relate("mimir:self-metrics-endpoint", "prometheus", model=model)
    # sh.juju.relate("mimir:grafana-dashboards-provider", "grafana", model=model)
    # sh.juju.relate("mimir:logging-consumer", "loki", model=model)
    # sh.juju.relate("mimir:grafana-source", "grafana", model=model)

    # assert wait_for_idle(apps=["prometheus", "loki", "grafana"], status="active", model=model)
    # assert wait_for_idle(apps=["mimir", "s3"], status="blocked", model=model)


async def test_grafana_source(ops_test: OpsTest):
    assert ops_test.model is not None
    grafana_leader: Unit = ops_test.model.applications["grafana"].units[0]  # type: ignore
    action = await grafana_leader.run_action("get-admin-password")
    action_result = await action.wait()
    admin_password = action_result.results["admin-password"]
    # admin_password = yaml.safe_load(sh.juju.run("grafana/0", "get-admin-password", model=model))[
    #     "admin-password"
    # ]

    grafana_token = sh.base64(f"admin:{admin_password}")
    # TODO: use the Grafana API once we can deploy with anonymous access
    datasources = sh.juju.ssh(
        f"--model={ops_test.model}",
        "grafana/0",
        "--container=grafana",
        "cat /etc/grafana/provisioning/datasources/datasources.yaml",
    )
    assert len(yaml.safe_load(datasources)["datasources"]) == 1


async def test_metrics_endpoint(ops_test: OpsTest):
    assert ops_test.model is not None
    juju_status = await ops_test.model.get_status()
    prometheus_url = await get_unit_address(ops_test, "prometheus", 0)
    response = requests.get(f"{prometheus_url}:9090/api/v1/targets")
    assert response.status_code == 200
    mimir_targets = [target for target in response.json()["data"]["activeTargets"] if target["discoveredLabels"]["juju_charm"] == "mimir-coordinator-k8s"]
    assert mimir_targets

# def test_mimir_cluster(ops_test: OpsTest):
    # await ops_test.model.deploy("mimir-worker-k8s", "worker", channel="latest/edge")
    # await ops_test.model.deploy("grafana-agent-k8s")

    # sh.juju.deploy("mimir-worker-k8s", "worker", channel="latest/edge", model=model)
    # sh.juju.config("worker", "role-all=true", model=model)
    # sh.juju.deploy("grafana-agent-k8s", "agent", channel="latest/edge", model=model)

    # sh.juju.relate("mimir:mimir-cluster", "worker", model=model)
    # sh.juju.relate("mimir:receive-remote-write", "agent", model=model)

    # run_seaweed("mimir", model=model)
    # sh.juju.run(
    #     "s3/leader", "sync-s3-credentials", "access-key=access", "secret-key=secret", model=model
    # )
    # sh.juju.relate("mimir:s3", "s3", model=model)

    # assert wait_for_idle(apps=["mimir", "worker", "s3", "agent"], status="active", model=model)

#     juju_status = json.loads(sh.juju.status(format="json", no_color=True, model=model))
#     hostname = juju_status["applications"]["mimir"]["units"]["mimir/0"]["address"]
#     mimir_url = f"{hostname}:8080"
#     response = requests.get(f"{mimir_url}/api/v1/query", params={"query": "up{juju_charm=~'grafana-agent-k8s'}"})

#     assert response.status_code == 200
#     assert len(json.loads(response.json())["data"]["result"]) == 1


# def test_tls(ops_test: OpsTest):
#     model = ops_test.model_name or ""
#     sh.juju.deploy("self-signed-certificates", "ca", model=model)
#     sh.juju.relate("mimir:certificates", "ca", model=model)

#     assert wait_for_idle(apps=["mimir", "ca"], status="active", model=model)
#     # TODO: make an https request to mimir


# def test_traefik(ops_test: OpsTest):
#     model = ops_test.model_name or ""
#     sh.juju.deploy("traefik-k8s", "traefik", model=model)
#     sh.juju.relate("mimir:ingress", "traefik", model=model)

#     assert wait_for_idle(apps=["mimir", "traefik"], status="active", model=model)
#     # TODO: try to reach Mimir through traefik
