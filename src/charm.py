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
import json
import logging

from interfaces.mimir_worker.v0.schema import ProviderSchema
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus

from mimir_coordinator import MimirCoordinator

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class MimirCoordinatorK8SOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        # food for thought: make MimirCoordinator ops-unaware and accept a
        # List[MimirRole].
        self.coordinator = MimirCoordinator(
            relations=self.mimir_worker_relations
        )

        # FIXME set status on correct occasion
        self.unit.status = ActiveStatus()

    @property
    def _s3_storage(self) -> dict:
        # if not self.model.relations['s3']:
        #     return {}
        return {
            "url": "foo",
            "endpoint": "bar",
            "access_key": "bar",
            "insecure": False,
            "secret_key": "x12",
        }

    @property
    def mimir_worker_relations(self):
        return self.model.relations.get('mimir_worker', [])

    def _on_config_changed(self, event):
        """Handle changed configuration.

        Change this example to suit your needs. If you don't need to handle config, you can remove
        this method.

        Learn more about config at https://juju.is/docs/sdk/config
        """
        hash_ring = []

        for relation in self.mimir_worker_relations:
            for remote_unit in relation.units:
                # todo: figure out under what circumstances this would not be routable
                unit_ip = relation.data[remote_unit]['private-address']
                hash_ring.append(unit_ip)

        for relation in self.mimir_worker_relations:
            relation.data[self.app]['config'] = json.dumps(dict(self.model.config))
            relation.data[self.app]['hash_ring'] = json.dumps(hash_ring)
            relation.data[self.app]['s3_storage'] = json.dumps(self._s3_storage)


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
