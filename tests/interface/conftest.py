# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import socket
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from coordinated_workers.interfaces.cluster import (
    ClusterRequirerAppData,
    ClusterRequirerUnitData,
)
from interface_tester import InterfaceTester
from ops import ActiveStatus
from ops.pebble import Layer
from scenario import Relation
from scenario.state import Container, Exec, State

from charm import MimirCoordinatorK8SOperatorCharm

nginx_container = Container(
    name="nginx",
    can_connect=True,
    layers={
        "foo": Layer(
            {  # type: ignore
                "summary": "foo",
                "description": "bar",
                "services": {
                    "nginx": {
                        "startup": "enabled",
                        "current": "active",
                        "name": "nginx",
                    }
                },
                "checks": {},
            }
        )
    },
    execs={
        Exec(
            [
                "mimirtool",
                "rules",
                "sync",
                f"--address=http://{socket.getfqdn()}:8080",
                "--id=anonymous",
            ],
            return_code=0,
        )
    },
)

nginx_prometheus_exporter_container = Container(
    name="nginx-prometheus-exporter",
    can_connect=True,
)

s3_relation = Relation(
    "s3",
    remote_app_data={
        "access-key": "key",
        "bucket": "mimir",
        "endpoint": "http://1.2.3.4:9000",
        "secret-key": "soverysecret",
    },
)
cluster_relation = Relation(
    "mimir-cluster",
    remote_app_data=dict(ClusterRequirerAppData(role="all").dump()),
    remote_units_data={
        0: ClusterRequirerUnitData(  # type: ignore
            address="http://example.com",
            juju_topology={"application": "app", "unit": "unit", "charm_name": "charmname"},  # type: ignore
        ).dump()
    },
)

grafana_source_relation = Relation(
    "grafana-source",
    remote_app_data={"datasources": json.dumps({"mimir/0": {"type": "mimir", "uid": "01234"}})},
)


@pytest.fixture(autouse=True, scope="module")
def patch_all():
    with ExitStack() as stack:
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(
            patch.multiple(
                "charms.observability_libs.v0.kubernetes_compute_resources_patch.KubernetesComputeResourcesPatch",
                _namespace="test-namespace",
                _patch=lambda _: None,
                is_ready=MagicMock(return_value=True),
                get_status=lambda _: ActiveStatus(""),
            )
        )
        stack.enter_context(charm_tracing_disabled())
        stack.enter_context(
            patch(
                "charm.MimirCoordinatorK8SOperatorCharm._ensure_mimirtool",
                MagicMock(return_value=None),
            )
        )
        yield


# Interface tests are centrally hosted at https://github.com/canonical/charm-relation-interfaces.
# this fixture is used by the test runner of charm-relation-interfaces to test mimir's compliance
# with the interface specifications.
# DO NOT MOVE OR RENAME THIS FIXTURE! If you need to, you'll need to open a PR on
# https://github.com/canonical/charm-relation-interfaces and change mimir's test configuration
# to include the new identifier/location.


@pytest.fixture
def grafana_datasource_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=MimirCoordinatorK8SOperatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[s3_relation, cluster_relation],
        ),
    )
    yield interface_tester


@pytest.fixture
def grafana_datasource_exchange_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=MimirCoordinatorK8SOperatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[s3_relation, cluster_relation, grafana_source_relation],
        ),
    )
    yield interface_tester
