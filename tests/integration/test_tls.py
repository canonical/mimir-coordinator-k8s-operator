#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from helpers import get_workload_file, oci_image
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
mc = SimpleNamespace(name="mc")

mimir_app_name = "coordinator"
ca_app_name = "ca"
app_names = [mimir_app_name, ca_app_name]


def get_nginx_config(ops_test: OpsTest):
    return get_workload_file(
        ops_test.model_name, mimir_app_name, 0, "nginx", "/etc/nginx/nginx.conf"
    )


@pytest.mark.abort_on_fail
async def test_nginx_config_has_ssl(ops_test: OpsTest):
    mimir_charm = await ops_test.build_charm(".")
    await asyncio.gather(
        ops_test.model.deploy(
            mimir_charm,
            resources={
                "nginx-image": oci_image("./metadata.yaml", "nginx-image"),
                "nginx-prometheus-exporter-image": oci_image(
                    "./metadata.yaml", "nginx-prometheus-exporter-image"
                ),
            },
            application_name="coordinator",
            trust=True,  # otherwise errors on ghwf (persistentvolumeclaims ... is forbidden)
        ),
        ops_test.model.deploy(
            "ch:self-signed-certificates",
            application_name="ca",
            channel="edge",
            trust=True,
        ),
    )

    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[mimir_app_name], status="blocked"),
        ops_test.model.wait_for_idle(apps=[ca_app_name], status="active"),
    )
    await ops_test.model.add_relation(mimir_app_name, ca_app_name)
    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[mimir_app_name], status="blocked"),
        ops_test.model.wait_for_idle(apps=[ca_app_name], status="active"),
    )

    nginx_config = get_nginx_config(ops_test).decode()
    assert "ssl_certificate /etc/nginx/certs/server.cert;" in nginx_config
    assert "ssl_certificate_key /etc/nginx/certs/server.key;" in nginx_config
