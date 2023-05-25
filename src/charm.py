#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""
import json
import logging
from typing import List

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.prometheus_k8s.v0.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from mimir_coordinator import MimirCoordinator
from ops.charm import CharmBase
from ops.main import main
from ops.model import Relation
from workload import WorkloadManager

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class MimirCoordinatorK8SOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.grafana_agent_workload = WorkloadManager(
            self,
            container_name="agent",
            config_getter=lambda: {},  # TODO
            status_changed_callback=self._update_unit_status,
        )

        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.framework.observe(
            self.on.ruler_relation_joined, self._on_ruler_joined  # pyright: ignore
        )

        # TODO: On any worker relation-joined/departed, need to updade grafana agent's scrape
        #  targets with the new memberlist.
        #  (Remote write would still be the same nginx-proxied endpoint.)

        # food for thought: make MimirCoordinator ops-unaware and accept a
        # List[MimirRole].
        self.coordinator = MimirCoordinator(relations=self.mimir_worker_relations)

        self.remote_write_consumer = PrometheusRemoteWriteConsumer(self)
        self.framework.observe(
            self.remote_write_consumer.on.endpoints_changed,  # pyright: ignore
            self._remote_write_endpoints_changed,
        )

        self.grafana_dashboard_provider = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboards-provider"
        )

        self.loki_consumer = LokiPushApiConsumer(self, relation_name="logging-consumer")
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_joined,  # pyright: ignore
            self._on_loki_relation_changed,
        )
        self.framework.observe(
            self.loki_consumer.on.loki_push_api_endpoint_departed,  # pyright: ignore
            self._on_loki_relation_changed,
        )

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
    def mimir_worker_relations(self) -> List[Relation]:
        """Returns the list of worker relations."""
        return self.model.relations.get("mimir_worker", [])

    def _on_config_changed(self, _):
        hash_ring = []

        for relation in self.mimir_worker_relations:
            for remote_unit in relation.units:
                # todo: figure out under what circumstances this would not be routable
                unit_ip = relation.data[remote_unit]["private-address"]
                hash_ring.append(unit_ip)

        for relation in self.mimir_worker_relations:
            relation.data[self.app]["config"] = json.dumps(dict(self.model.config))
            relation.data[self.app]["hash_ring"] = json.dumps(hash_ring)
            relation.data[self.app]["s3_storage"] = json.dumps(self._s3_storage)

    def _remote_write_endpoints_changed(self, _):
        # TODO Update grafana-agent config file with the new external prometheus's endpoint
        pass

    def _on_ruler_joined(self, _):
        # TODO Update relation data with the rule files (metrics + logs)
        pass

    def _on_loki_relation_changed(self, _):
        # TODO Update rules relation with the new list of Loki push-api endpoints
        pass

    def _update_unit_status(self, *_):
        self.unit.status = self.grafana_agent_workload.status()


if __name__ == "__main__":  # pragma: nocover
    main(MimirCoordinatorK8SOperatorCharm)
