#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
import yaml
from helpers import deploy_literal_bundle
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
mc = SimpleNamespace(name="mc")


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms."""
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".")

    test_bundle = dedent(
        f"""
        ---
        bundle: kubernetes
        name: test-charm
        applications:
          {mc.name}:
            charm: {charm}
            trust: true
            resources:
              nginx-image: {METADATA["resources"]["nginx-image"]["upstream-source"]}
              nginx-prometheus-exporter-image: {METADATA["resources"]["nginx-prometheus-exporter-image"]["upstream-source"]}
            scale: 1
          loki:
            charm: loki-k8s
            trust: true
            channel: edge
            scale: 1
          prometheus:
            charm: prometheus-k8s
            trust: true
            channel: edge
            scale: 1
          grafana:
            charm: grafana-k8s
            trust: true
            channel: edge
            scale: 1

        relations:
        - [mc:logging-consumer, loki:logging]
        - [mc:self-metrics-endpoint, prometheus:metrics-endpoint]
        - [mc:grafana-dashboards-provider, grafana:grafana-dashboard]
    """
    )

    # Deploy the charm and wait for active/idle status
    await deploy_literal_bundle(ops_test, test_bundle)  # See appendix below
    await ops_test.model.wait_for_idle(
        apps=["loki", "prometheus", "grafana"],
        status="active",
        raise_on_error=False,
        timeout=600,
        idle_period=30,
    )
    await ops_test.model.wait_for_idle(
        apps=[mc.name], status="blocked", raise_on_error=False, timeout=600, idle_period=30
    )
