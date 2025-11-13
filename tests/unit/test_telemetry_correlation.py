# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json

from ops.testing import Relation, State


def test_metrics_to_traces_config_exemplars_disabled(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN a datasource exchange relations with a tempo type
    relations = [
        s3,
        all_worker,
        Relation(
            "grafana-source",
            remote_app_name="grafana",
            remote_app_data={
                "datasource_uids": json.dumps({"mimir/0": "1234"}),
                "grafana_uid": "graf_1",
            },
        ),
        Relation(
            "send-datasource",
            remote_app_name="tempo",
            remote_app_data={"datasources": json.dumps([{"type": "tempo", "uid": "tempo_1", "grafana_uid": "graf_1"}])},
        ),
    ]
    # AND exemplars are disabled
    state_in = State(
        config={"max_global_exemplars_per_user":0},
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )
    # WHEN we run any event
    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        config = charm._build_metrics_to_traces_config()
        # THEN exemplarTraceIdDestinations config is not generated
        assert "exemplarTraceIdDestinations" not in (config or {})


def test_metrics_to_traces_config_no_datasources(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN no datasources exchange relations
    relations = [
        s3,
        all_worker,
    ]
    # AND exemplars are enabled
    state_in = State(
        config={"max_global_exemplars_per_user":1},
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )
    # WHEN we run any event
    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        config = charm._build_metrics_to_traces_config()
        # THEN exemplarTraceIdDestinations config is not generated
        assert "exemplarTraceIdDestinations" not in (config or {})


def test_metrics_to_traces_config_non_tempo_datasources(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN datasources exchange relations only with non-tempo types
    relations = [
        s3,
        all_worker,
        Relation(
            "send-datasource",
            remote_app_data={"datasources": json.dumps([{"type": "loki", "uid": "loki_1", "grafana_uid": "graf_1"}])},
        )
    ]
    # AND exemplars are enabled
    state_in = State(
        config={"max_global_exemplars_per_user":1},
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )
    # WHEN we run any event
    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        config = charm._build_metrics_to_traces_config()
        # THEN exemplarTraceIdDestinations config is not generated
        assert "exemplarTraceIdDestinations" not in (config or {})



def test_metrics_to_traces_config(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN a datasource exchange relations with a tempo type
    relations = [
        s3,
        all_worker,
        Relation(
            "grafana-source",
            remote_app_name="grafana",
            remote_app_data={
                "datasource_uids": json.dumps({"mimir/0": "1234"}),
                "grafana_uid": "graf_1",
            },
        ),
        Relation(
            "send-datasource",
            remote_app_name="tempo",
            remote_app_data={"datasources": json.dumps([{"type": "tempo", "uid": "tempo_1", "grafana_uid": "graf_1"}])},
        ),
    ]
    # AND exemplars are enabled
    state_in = State(
        config={"max_global_exemplars_per_user":1},
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )
    # WHEN we run any event
    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        config = charm._build_metrics_to_traces_config()
        # THEN exemplarTraceIdDestinations config is generated
        assert "exemplarTraceIdDestinations" in config
        # AND it contains the remote tempo datasource uid
        assert config["exemplarTraceIdDestinations"]
        assert config["exemplarTraceIdDestinations"][0]["datasourceUid"] == "tempo_1"
