# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json

import scenario
from cosl.interfaces.datasource_exchange import (
    DatasourceExchange,
    DSExchangeAppData,
    GrafanaDatasource,
)
from scenario import Relation, State


def test_datasource_send(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN a regular HA deployment and two ds_exchange integrations with a tempo and a loki
    ds_tempo = [
        {"type": "tempo", "uid": "3", "grafana_uid": "4"},
    ]

    ds_loki = [
        {"type": "loki", "uid": "8", "grafana_uid": "9"},
    ]

    loki_dsx = Relation(
        "send-datasource",
        remote_app_data=DSExchangeAppData(
            datasources=json.dumps(sorted(ds_loki, key=lambda raw_ds: raw_ds["uid"]))  # type: ignore
        ).dump(),
    )
    tempo_dsx = Relation(
        "send-datasource",
        remote_app_data=DSExchangeAppData(
            datasources=json.dumps(sorted(ds_tempo, key=lambda raw_ds: raw_ds["uid"]))  # type: ignore
        ).dump(),
    )

    ds = Relation(
        "grafana-source",
        remote_app_data={
            "grafana_uid": "foo-something-bars",
            "datasource_uids": json.dumps({"mimir/0": "1234"}),
        },
    )

    state_in = State(
        relations=[
            s3,
            all_worker,
            ds,
            loki_dsx,
            tempo_dsx,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
    )

    # WHEN we receive any event
    with context(context.on.update_status(), state_in) as mgr:
        charm = mgr.charm
        # THEN we can find all received datasource uids in the coordinator
        dsx: DatasourceExchange = charm.coordinator.datasource_exchange
        received = dsx.received_datasources
        assert received == (
            GrafanaDatasource(type="tempo", uid="3", grafana_uid="4"),
            GrafanaDatasource(type="loki", uid="8", grafana_uid="9"),
        )
        state_out = mgr.run()

    # AND THEN we forward our own datasource information to tempo and loki
    assert state_out.unit_status.name == "active"
    published_dsx_loki = state_out.get_relation(loki_dsx.id).local_app_data
    published_dsx_tempo = state_out.get_relation(tempo_dsx.id).local_app_data
    assert published_dsx_tempo == published_dsx_loki
    assert json.loads(published_dsx_tempo["datasources"])[0] == {
        "type": "prometheus",
        "uid": "1234",
        "grafana_uid": "foo-something-bars",
    }
