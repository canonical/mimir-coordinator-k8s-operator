#!/usr/bin/env python3
# Copyright 2024 Ubuntu
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
coord = SimpleNamespace(name="coord")


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
          - coord:metrics-endpoint
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

    # TODO: Once we close this issue in cos-lib: https://github.com/canonical/cos-lib/issues/24
    #
    # - Verify that the scrape jobs for nginx and the workers are in Prometheus
    # - Verify the alert rules from nginx, and the workers are in Prometheus
    # - Verify the record rules from nginx, and the workers are in Prometheus
