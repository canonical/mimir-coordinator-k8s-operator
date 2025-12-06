import json
from typing import Any, Dict, List
from urllib.parse import quote

import requests
import yaml
from juju.application import Application
from juju.unit import Unit
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from minio import Minio
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_trace_id
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_fixed


def charm_resources(metadata_file="charmcraft.yaml") -> Dict[str, str]:
    with open(metadata_file, "r") as file:
        metadata = yaml.safe_load(file)
    resources = {}
    for res, data in metadata["resources"].items():
        resources[res] = data["upstream-source"]
    return resources


async def configure_minio(ops_test: OpsTest):
    bucket_name = "mimir"
    minio_leader_unit_number = await get_leader_unit_number(ops_test, "minio")
    minio_addr = await get_unit_address(ops_test, "minio", minio_leader_unit_number)
    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key="access",
        secret_key="secretsecret",
        secure=False,
    )
    # create bucket
    found = mc_client.bucket_exists(bucket_name)
    if not found:
        mc_client.make_bucket(bucket_name)


async def configure_s3_integrator(ops_test: OpsTest):
    assert ops_test.model is not None
    bucket_name = "mimir"
    config = {
        "access-key": "access",
        "secret-key": "secretsecret",
    }
    s3_integrator_app: Application = ops_test.model.applications["s3"]  # type: ignore
    s3_integrator_leader: Unit = s3_integrator_app.units[0]

    await s3_integrator_app.set_config(
        {
            "endpoint": f"minio-0.minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
            "bucket": bucket_name,
        }
    )
    action = await s3_integrator_leader.run_action("sync-s3-credentials", **config)
    action_result = await action.wait()
    assert action_result.status == "completed"

async def get_leader_unit_number(ops_test: OpsTest, app_name: str) -> int:
    """Get the unit number of the leader of an application.

    Raises an exception if no leader is found.
    """
    assert ops_test.model
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    if app is None:
        raise ValueError(f"no app exists with name {app_name}")

    for name, unit in app["units"].items():
        if unit["leader"]:
            return int(name.split("/")[1])

    raise ValueError(f"no leader found for app {app_name}")


async def get_unit_address(ops_test: OpsTest, app_name: str, unit_no: int) -> str:
    assert ops_test.model is not None
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    if app is None:
        assert False, f"no app exists with name {app_name}"
    unit = app["units"].get(f"{app_name}/{unit_no}")
    if unit is None:
        assert False, f"no unit exists in app {app_name} with index {unit_no}"
    return unit["address"]


async def get_grafana_datasources_from_client_localhost(
    ops_test: OpsTest,
    grafana_app: str = "grafana",
) -> List[Any]:
    """Get Grafana datasources from the test host machine (outside the cluster)."""
    assert ops_test.model is not None
    grafana_leader: Unit = ops_test.model.applications[grafana_app].units[0]  # type: ignore
    action = await grafana_leader.run_action("get-admin-password")
    action_result = await action.wait()
    admin_password = action_result.results["admin-password"]
    leader_unit_number = await get_leader_unit_number(ops_test, grafana_app)
    grafana_url = await get_unit_address(ops_test, grafana_app, leader_unit_number)
    url = f"http://admin:{admin_password}@{grafana_url}:3000/api/datasources"

    # Run query from host
    response = requests.get(url)
    assert response.status_code == 200
    return response.json()


async def get_grafana_datasources_from_client_pod(
    ops_test: OpsTest,
    source_pod: str,
    grafana_app: str = "grafana",
) -> List[Any]:
    """Get Grafana datasources from inside a pod (within the cluster)."""
    assert ops_test.model is not None
    grafana_leader: Unit = ops_test.model.applications[grafana_app].units[0]  # type: ignore
    action = await grafana_leader.run_action("get-admin-password")
    action_result = await action.wait()
    admin_password = action_result.results["admin-password"]
    leader_unit_number = await get_leader_unit_number(ops_test, grafana_app)
    grafana_url = await get_unit_address(ops_test, grafana_app, leader_unit_number)
    url = f"http://admin:{admin_password}@{grafana_url}:3000/api/datasources"

    # Run query from within a pod using juju exec (needed for service mesh)
    action = await ops_test.model.applications[source_pod.split("/")[0]].units[
        int(source_pod.split("/")[1])
    ].run(f"curl -s {url}")
    result = await action.wait()

    response_text = result.results.get("stdout", result.results.get("Stdout", ""))
    return json.loads(response_text)


async def get_prometheus_targets_from_client_localhost(
    ops_test: OpsTest,
    prometheus_app: str = "prometheus",
) -> Dict[str, Any]:
    """Get Prometheus scrape targets from the test host machine (outside the cluster)."""
    assert ops_test.model is not None
    leader_unit_number = await get_leader_unit_number(ops_test, prometheus_app)
    prometheus_url = await get_unit_address(ops_test, prometheus_app, leader_unit_number)
    url = f"http://{prometheus_url}:9090/api/v1/targets"

    # Run query from host
    response = requests.get(url)
    assert response.status_code == 200
    response_json = response.json()

    assert response_json["status"] == "success"
    return response_json["data"]


async def get_prometheus_targets_from_client_pod(
    ops_test: OpsTest,
    source_pod: str,
    prometheus_app: str = "prometheus",
) -> Dict[str, Any]:
    """Get Prometheus scrape targets from inside a pod (within the cluster)."""
    assert ops_test.model is not None
    leader_unit_number = await get_leader_unit_number(ops_test, prometheus_app)
    prometheus_url = await get_unit_address(ops_test, prometheus_app, leader_unit_number)
    url = f"http://{prometheus_url}:9090/api/v1/targets"

    # Run query from within a pod using juju exec (needed for service mesh)
    action = await ops_test.model.applications[source_pod.split("/")[0]].units[
        int(source_pod.split("/")[1])
    ].run(f"curl -s {url}")
    result = await action.wait()

    response_text = result.results.get("stdout", result.results.get("Stdout", ""))
    response_json = json.loads(response_text)

    assert response_json["status"] == "success"
    return response_json["data"]


async def query_mimir_from_client_localhost(
    ops_test: OpsTest,
    query: str,
    coordinator_app: str = "mimir",
) -> Dict[str, Any]:
    """Query Mimir API from the test host machine (outside the cluster)."""
    assert ops_test.model is not None

    # Run query from host
    leader_unit_number = await get_leader_unit_number(ops_test, coordinator_app)
    mimir_url = await get_unit_address(ops_test, coordinator_app, leader_unit_number)
    response = requests.get(
        f"http://{mimir_url}:8080/prometheus/api/v1/query",
        params={"query": query},
    )
    assert response.status_code == 200
    response_json = response.json()

    assert response_json["status"] == "success"
    return response_json["data"]["result"]


async def query_mimir_from_client_pod(
    ops_test: OpsTest,
    source_pod: str,
    query: str,
    coordinator_app: str = "mimir",
) -> Dict[str, Any]:
    """Query Mimir API from inside a pod (within the cluster)."""
    assert ops_test.model is not None

    # Run query from within a pod using juju exec (needed for service mesh)
    mimir_url = f"{coordinator_app}.{ops_test.model.name}.svc.cluster.local"
    url = f"http://{mimir_url}:8080/prometheus/api/v1/query"
    encoded_query = quote(query, safe='')

    action = await ops_test.model.applications[source_pod.split("/")[0]].units[
        int(source_pod.split("/")[1])
    ].run(f"curl -s '{url}?query={encoded_query}'")
    result = await action.wait()

    response_text = result.results.get("stdout", result.results.get("Stdout", ""))
    response_json = json.loads(response_text)

    assert response_json["status"] == "success"
    return response_json["data"]["result"]


async def get_traefik_proxied_endpoints(
    ops_test: OpsTest, traefik_app: str = "traefik"
) -> Dict[str, Any]:
    assert ops_test.model is not None
    traefik_leader: Unit = ops_test.model.applications[traefik_app].units[0]  # type: ignore
    action = await traefik_leader.run_action("show-proxied-endpoints")
    action_result = await action.wait()
    return json.loads(action_result.results["proxied-endpoints"])

async def push_to_otelcol(ops_test: OpsTest, metric_name: str) -> str:
    """Push a metric along with a trace ID to an Opentelemetry Collector that is related to Mimir so that the exemplar can be stored in Mimir.

    This block creates an exemplars by attaching a trace ID provided by the Opentelemetry SDK to a metric.
    Please visit https://opentelemetry.io/docs/languages/python/instrumentation/ for more info on how the instrumentation works and/or how to modify it.
    """
    leader_unit_number = await get_leader_unit_number(ops_test, "otelcol")
    otel_url = await get_unit_address(ops_test, "otelcol", leader_unit_number)
    collector_endpoint = f"http://{otel_url}:4318/v1/metrics"

    resource = Resource(attributes={
        SERVICE_NAME: "service",
        SERVICE_VERSION: "1.0.0"
    })

    otlp_exporter = OTLPMetricExporter(endpoint=collector_endpoint)
    metric_reader = PeriodicExportingMetricReader(otlp_exporter, export_interval_millis=5000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter("meter", "1.0.0")
    counter = meter.create_counter(metric_name, description="A placeholder counter metric")
    tracer_provider = TracerProvider()

    with tracer_provider.get_tracer("service").start_as_current_span("generate_metrics_span") as span:
        span_ctx = span.get_span_context()
        trace_id = span_ctx.trace_id

        trace_id_hex = format_trace_id(trace_id)

        counter.add(100, {"trace_id":trace_id_hex})

    return trace_id_hex

@retry(wait=wait_fixed(20), stop=stop_after_attempt(6))
async def query_exemplars(
    ops_test: OpsTest, query_name: str, coordinator_app: str
) -> str | None:

    leader_unit_number = await get_leader_unit_number(ops_test, coordinator_app)
    mimir_url = await get_unit_address(ops_test, coordinator_app, leader_unit_number)

    response = requests.get(f"http://{mimir_url}:8080/prometheus/api/v1/query_exemplars", params={'query': f"{query_name}_total"})

    assert response.status_code == 200

    response_data = response.json()

    assert response_data.get("data", []), "No exemplar data found in Mimir's API."

    # Check if the exemplar with the trace_id is present in the response
    exemplars = response_data["data"][0].get("exemplars", [])

    assert exemplars, "No exemplars found in data returned from Mimir"
    assert exemplars[0].get("labels", {})

    # Find the `trace_id` from the first exemplar's labels
    assert exemplars[0].get("labels").get("trace_id"), "No trace_id found in data returned from Mimir"
    trace_id = exemplars[0].get("labels").get("trace_id")

    return trace_id


# TODO: this is a workaround. the ingress provider should provide the proxied-endpoints. See https://github.com/canonical/istio-ingress-k8s-operator/issues/108.
# Update this after the above issue is fixed.
def get_istio_ingress_ip(ops_test: OpsTest, app_name: str = "istio-ingress"):
    """Get the istio-ingress public IP address from Kubernetes."""
    gateway_resource = create_namespaced_resource(
        group="gateway.networking.k8s.io",
        version="v1",
        kind="Gateway",
        plural="gateways",
    )
    client = Client()
    gateway = client.get(gateway_resource, app_name, namespace=ops_test.model.name)  # type: ignore
    if gateway.status and gateway.status.get("addresses"):  # type: ignore
        return gateway.status["addresses"][0]["value"]  # type: ignore
    raise ValueError(f"No ingress address found for {app_name}")


async def service_mesh(
    enable: bool,
    ops_test: OpsTest,
    beacon_app_name: str,
    apps_to_be_related_with_beacon: List[str],
):
    """Enable or disable the service-mesh in the model.

    This puts the entire model, that the beacon app is part of, on mesh.
    This integrates the apps_to_be_related_with_beacon with the beacon app via the `service-mesh` relation.
    """
    assert ops_test.model is not None
    await ops_test.model.applications[beacon_app_name].set_config(
        {"model-on-mesh": str(enable).lower()}
    )
    # Wait for all active state before further actions.
    # The wait is necessary to make sure all the charms have recovered from the network changes.
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=1000,
        raise_on_error=False,
    )
    if enable:
        for app in apps_to_be_related_with_beacon:
            await ops_test.model.integrate(f"{beacon_app_name}:service-mesh", f"{app}:service-mesh")
    else:
        for app in apps_to_be_related_with_beacon:
            await ops_test.model.applications[beacon_app_name].remove_relation(
                f"{beacon_app_name}:service-mesh", f"{app}:service-mesh"
            )
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=1000,
        idle_period=30,
        raise_on_error=False,
    )
