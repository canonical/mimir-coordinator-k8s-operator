import logging
from typing import Any, Dict

from tenacity import retry, wait_fixed, stop_after_attempt
import yaml
import requests
from juju.application import Application
from juju.unit import Unit
from minio import Minio
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


def charm_resources(metadata_file="metadata.yaml") -> Dict[str, str]:
    with open(metadata_file, "r") as file:
        metadata = yaml.safe_load(file)
    resources = {}
    for res, data in metadata["resources"].items():
        resources[res] = data["upstream-source"]
    return resources


async def configure_minio(ops_test: OpsTest):
    bucket_name = "mimir"
    minio_addr = await get_unit_address(ops_test, "minio", 0)
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


async def get_unit_address(ops_test: OpsTest, app_name: str, unit_no: int):
    assert ops_test.model is not None
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    if app is None:
        assert False, f"no app exists with name {app_name}"
    unit = app["units"].get(f"{app_name}/{unit_no}")
    if unit is None:
        assert False, f"no unit exists in app {app_name} with index {unit_no}"
    return unit["address"]

@retry(wait=wait_fixed(10), stop=stop_after_attempt(10))
async def check_agent_data_in_mimir(ops_test: OpsTest, coordinator_app: str) -> Dict[str, Any]:
    mimir_url = await get_unit_address(ops_test, coordinator_app, 0)
    response = requests.get(f"http://{mimir_url}:8080/status")
    assert response.status_code == 200

    response = requests.get(
        f"http://{mimir_url}:8080/prometheus/api/v1/query",
        params={"query": 'up{juju_charm=~"grafana-agent-k8s"}'},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"  # the query was successful
    assert response.json()["data"]["result"]
