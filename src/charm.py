#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging
from typing import List

from interfaces.mimir_worker.v0.schema import ProviderSchema
from ops.charm import CharmBase
from ops.main import main

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


def is_coherent(roles: List[str]):
    """Return True if the roles list makes up a coherent mimir deployment."""
    return False


class MimirCoordinatorK8SOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        ProviderSchema(
            app={
                "hash_ring": '["bla"]',
                "config": '{"key":"value"}',
                "s3_config": '{"url":"dd", "endpoint":"dd", "secret_key":"", "access_key":"", "insecure":"false"}',
            }
        )

    def _on_config_changed(self, event):
        """Handle changed configuration.

        Change this example to suit your needs. If you don't need to handle config, you can remove
        this method.

        Learn more about config at https://juju.is/docs/sdk/config
        """
        pass


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
