#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
import yaml
from helpers import deploy_literal_bundle, run_command
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
coord = SimpleNamespace(name="coord")
apps = ["coord", "write", "read", "prom"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms."""
    charm = await ops_test.build_charm(".")

    test_bundle = dedent(
        f"""
        ---
        bundle: kubernetes
        name: test-charm
        applications:
          {coord.name}:
            charm: {charm}
            trust: true
            resources:
              nginx-image: {METADATA["resources"]["nginx-image"]["upstream-source"]}
              nginx-prometheus-exporter-image: {METADATA["resources"]["nginx-prometheus-exporter-image"]["upstream-source"]}
            scale: 1
          prom:
            charm: prometheus-k8s
            channel: edge
            scale: 1
            trust: true
          read:
            charm: mimir-worker-k8s
            channel: edge
            scale: 1
            constraints: arch=amd64
            options:
              alertmanager: true
              compactor: true
              querier: true
              query-frontend: true
              query-scheduler: true
              ruler: true
              store-gateway: true
            trust: true
          write:
            charm: mimir-worker-k8s
            channel: edge
            scale: 1
            constraints: arch=amd64
            options:
              compactor: true
              distributor: true
              ingester: true
            trust: true
        relations:
        - - prom:metrics-endpoint
          - coord:self-metrics-endpoint
        - - coord:mimir-cluster
          - read:mimir-cluster
        - - coord:mimir-cluster
          - write:mimir-cluster
        """
    )

    # Deploy the charm and wait for active/idle status
    await deploy_literal_bundle(ops_test, test_bundle)  # See appendix below
    await ops_test.model.wait_for_idle(
        apps=["read", "write", "prom"],
        status="active",
        raise_on_error=False,
        timeout=600,
        idle_period=30,
    )

    await ops_test.model.wait_for_idle(
        apps=[coord.name], status="blocked", raise_on_error=False, timeout=600, idle_period=30
    )


@pytest.mark.abort_on_fail
async def test_scrape_jobs(ops_test: OpsTest):
    # Check scrape jobs
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/targets"]
    result = await run_command(ops_test.model_name, "prom", 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))

    active_targets = result_json["data"]["activeTargets"]

    for at in active_targets:
        assert at["labels"]["juju_application"] in apps


@pytest.mark.abort_on_fail
async def test_rules(ops_test: OpsTest):
    # Check Rules
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/rules"]
    result = await run_command(ops_test.model_name, "prom", 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    groups = result_json["data"]["groups"]

    for group in groups:
        for rule in group["rules"]:
            assert rule["labels"]["juju_application"] in apps
