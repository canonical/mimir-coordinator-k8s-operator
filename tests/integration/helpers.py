# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


async def deploy_literal_bundle(ops_test: OpsTest, bundle: str):
    run_args = [
        "juju",
        "deploy",
        "--trust",
        "-m",
        ops_test.model_name,
        str(ops_test.render_bundle(bundle)),
    ]

    retcode, stdout, stderr = await ops_test.run(*run_args)
    assert retcode == 0, f"Deploy failed: {(stderr or stdout).strip()}"
    logger.info(stdout)
