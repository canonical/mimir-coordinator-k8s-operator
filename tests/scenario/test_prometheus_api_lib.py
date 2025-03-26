"""Tests for the prometheus-api lib requirer and provider classes, excluding their usage in the Mimir coordinator."""

from typing import Union

import pytest
from charms.mimir_coordinator_k8s.v0.prometheus_api import (
    PrometheusApiAppData,
    PrometheusApiProvider,
    PrometheusApiRequirer,
)
from ops import CharmBase
from ops.testing import Context, Relation, State

RELATION_NAME = "app-data-relation"
INTERFACE_NAME = "app-data-interface"

# Note: if this is changed, the PrometheusApiAppData concrete classes below need to change their constructors to match
SAMPLE_APP_DATA = PrometheusApiAppData(
    ingress_url="http://www.ingress-url.com/prometheus",
    direct_url="http://www.internal-url.com/prometheus",
)
SAMPLE_APP_DATA_2 = PrometheusApiAppData(
    ingress_url="http://www.ingress-url2.com/prometheus",
    direct_url="http://www.internal-url2.com/prometheus",
)
SAMPLE_APP_DATA_NO_INGRESS_URL = PrometheusApiAppData(
    ingress_url="http://www.ingress-url.com/prometheus",
    direct_url="http://www.internal-url.com/prometheus",
)


class PrometheusApiProviderCharm(CharmBase):
    META = {
        "name": "provider",
        "provides": {RELATION_NAME: {"interface": RELATION_NAME}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_provider = PrometheusApiProvider(
            self.model.relations, app=self.app, relation_name=RELATION_NAME
        )


@pytest.fixture()
def prometheus_api_provider_context():
    return Context(charm_type=PrometheusApiProviderCharm, meta=PrometheusApiProviderCharm.META)


class PrometheusApiRequirerCharm(CharmBase):
    META = {
        "name": "requirer",
        "requires": {RELATION_NAME: {"interface": "prometheus-api"}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_requirer = PrometheusApiRequirer(
            self.model.relations, relation_name=RELATION_NAME
        )


@pytest.fixture()
def prometheus_api_requirer_context():
    return Context(charm_type=PrometheusApiRequirerCharm, meta=PrometheusApiRequirerCharm.META)


@pytest.mark.parametrize("data", [SAMPLE_APP_DATA, SAMPLE_APP_DATA_NO_INGRESS_URL])
def test_prometheus_api_provider_sends_data_correctly(data, prometheus_api_provider_context):
    """Tests that a charm using PrometheusApiProvider sends the correct data during publish."""
    # Arrange
    prometheus_api_relation = Relation(RELATION_NAME, INTERFACE_NAME, local_app_data={})
    relations = [prometheus_api_relation]
    state = State(relations=relations, leader=True)

    # Act
    with prometheus_api_provider_context(
        # construct a charm using an event that won't trigger anything here
        prometheus_api_provider_context.on.update_status(),
        state=state,
    ) as manager:
        manager.charm.relation_provider.publish(**data.model_dump())

        # Assert
        # Convert local_app_data to TempoApiAppData for comparison
        prometheus_api_relation_out = manager.ops.state.get_relation(prometheus_api_relation.id)
        actual = PrometheusApiAppData.model_validate(
            dict(prometheus_api_relation_out.local_app_data)
        )
        assert actual == data


@pytest.mark.parametrize(
    "relations, expected_data",
    [
        # no relations
        ([], None),
        # one empty relation
        (
            [Relation(RELATION_NAME, INTERFACE_NAME, remote_app_data={})],
            None,
        ),
        # one populated relation
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=SAMPLE_APP_DATA.model_dump(mode="json"),
                )
            ],
            SAMPLE_APP_DATA,
        ),
        # one populated relation without ingress_url
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=SAMPLE_APP_DATA_NO_INGRESS_URL.model_dump(mode="json"),
                )
            ],
            SAMPLE_APP_DATA_NO_INGRESS_URL,
        ),
    ],
)
def test_prometheus_api_requirer_get_data(
    relations, expected_data, prometheus_api_requirer_context
):
    """Tests that PrometheusApiRequirer.get_data() returns correctly."""
    state = State(
        relations=relations,
        leader=False,
    )

    with prometheus_api_requirer_context(
        prometheus_api_requirer_context.on.update_status(), state=state
    ) as manager:
        charm = manager.charm

        data = charm.relation_requirer.get_data()
        assert are_app_data_equal(data, expected_data)


def are_app_data_equal(
    data1: Union[PrometheusApiAppData, None], data2: Union[PrometheusApiAppData, None]
):
    """Compare two PrometheusApiRequirer objects, tolerating when one or both is None."""
    if data1 is None and data2 is None:
        return True
    if data1 is None or data2 is None:
        return False
    return data1.model_dump() == data2.model_dump()
