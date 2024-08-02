import logging
from time import sleep
from typing import Dict

import requests
import yaml
from juju.application import Application
from juju.unit import Unit
from minio import Minio
from pytest_operator.plugin import OpsTest

# pyright: reportAttributeAccessIssue=false

# _JUJU_DATA_CACHE = {}
# _JUJU_KEYS = ("egress-subnets", "ingress-address", "private-address")
# ACCESS_KEY = "accesskey"
# SECRET_KEY = "secretkey"
# MINIO = "minio"
# BUCKET_NAME = "mimir"
# S3_INTEGRATOR = "s3-integrator"
# WORKER_NAME = "mimir-worker"
# APP_NAME = "mimir"

logger = logging.getLogger(__name__)


def charm_resources(metadata_file="metadata.yaml") -> Dict[str, str]:
    with open(metadata_file, "r") as file:
        metadata = yaml.safe_load(file)
    resources = {}
    for res, data in metadata["resources"].items():
        resources[res] = data["upstream-source"]
    return resources


# def _check_idle(apps: List[str], model: str, status: Literal["active", "blocked"]) -> bool:
#     juju_status = json.loads(sh.juju.status(format="json", no_color=True, model=model))
#     success = True
#     for app in apps:
#         scale = juju_status["applications"][app]["scale"]
#         for unit_number in range(0, scale):
#             workload_status = juju_status["applications"][app]["units"][f"{app}/{unit_number}"][
#                 "workload-status"
#             ]["current"]
#             unit_status = juju_status["applications"][app]["units"][f"{app}/{unit_number}"][
#                 "juju-status"
#             ]["current"]
#             if workload_status != status or unit_status != "idle":
#                 success = False
#                 logger.info(
#                     f"{app}/{unit_number} are in {workload_status}/{unit_status} (expected {status}/idle)"
#                 )

#     return success


# def wait_for_idle(
#     apps: List[str],
#     status: Literal["active", "blocked"],
#     model: str,
#     attempts: int = 10,
#     idle_period: int = 30,
# ) -> bool:
#     time_before_next_check = 10  # TODO: make this a function opt arg
#     for attempt in range(0, attempts):
#         if _check_idle(apps, model, status):
#             sleep(float(time_before_next_check))
#             if _check_idle(apps, model, status):
#                 logger.info(f"{apps} is in the desired status")
#                 return True
#         print(f"Waiting for {apps} to settle... ({attempt+1})")
#         sleep(float(idle_period))
#     return False


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


def wait_for_prometheus_query(url: str, query: str, attempts: int = 10, waiting_time=30) -> None:
    for attempt in range(0, attempts):
        response = requests.get(url, params={"query": query})
        assert response.status_code == 200
        assert response.json()["status"] == "success"  # the query was successful
        if not response.json()["data"]["result"]:  # grafana agent's data is in Mimir
            sleep(float(waiting_time))

    assert response.json()["data"]["result"]  # grafana agent's data is in Mimir


# def get_unit_info(unit_name: str, model: str = None) -> dict:
#     """Return unit-info data structure.

#      for example:

#     traefik-k8s/0:
#       opened-ports: []
#       charm: local:focal/traefik-k8s-1
#       leader: true
#       relation-info:
#       - endpoint: ingress-per-unit
#         related-endpoint: ingress
#         application-data:
#           _supported_versions: '- v1'
#         related-units:
#           prometheus-k8s/0:
#             in-scope: true
#             data:
#               egress-subnets: 10.152.183.150/32
#               ingress-address: 10.152.183.150
#               private-address: 10.152.183.150
#       provider-id: traefik-k8s-0
#       address: 10.1.232.144
#     """
#     cmd = f"juju show-unit {unit_name}".split(" ")
#     if model:
#         cmd.insert(2, "-m")
#         cmd.insert(3, model)

#     proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
#     raw_data = proc.stdout.read().decode("utf-8").strip()

#     data = yaml.safe_load(raw_data) if raw_data else None

#     if not data:
#         raise ValueError(
#             f"no unit info could be grabbed for {unit_name}; "
#             f"are you sure it's a valid unit name?"
#             f"cmd={' '.join(proc.args)}"
#         )

#     if unit_name not in data:
#         raise KeyError(unit_name, f"not in {data!r}")

#     unit_data = data[unit_name]
#     _JUJU_DATA_CACHE[unit_name] = unit_data
#     return unit_data


# def get_relation_by_endpoint(relations, local_endpoint, remote_endpoint, remote_obj):
#     matches = [
#         r
#         for r in relations
#         if (
#             (r["endpoint"] == local_endpoint and r["related-endpoint"] == remote_endpoint)
#             or (r["endpoint"] == remote_endpoint and r["related-endpoint"] == local_endpoint)
#         )
#         and remote_obj in r["related-units"]
#     ]
#     if not matches:
#         raise ValueError(
#             f"no matches found with endpoint=="
#             f"{local_endpoint} "
#             f"in {remote_obj} (matches={matches})"
#         )
#     if len(matches) > 1:
#         raise ValueError(
#             "multiple matches found with endpoint=="
#             f"{local_endpoint} "
#             f"in {remote_obj} (matches={matches})"
#         )
#     return matches[0]


# @dataclass
# class UnitRelationData:
#     unit_name: str
#     endpoint: str
#     leader: bool
#     application_data: Dict[str, str]
#     unit_data: Dict[str, str]


# def get_content(
#     obj: str, other_obj, include_default_juju_keys: bool = False, model: str = None
# ) -> UnitRelationData:
#     """Get the content of the databag of `obj`, as seen from `other_obj`."""
#     unit_name, endpoint = obj.split(":")
#     other_unit_name, other_endpoint = other_obj.split(":")

#     unit_data, app_data, leader = get_databags(
#         unit_name, endpoint, other_unit_name, other_endpoint, model
#     )

#     if not include_default_juju_keys:
#         purge(unit_data)

#     return UnitRelationData(unit_name, endpoint, leader, app_data, unit_data)


# def get_databags(local_unit, local_endpoint, remote_unit, remote_endpoint, model):
#     """Get the databags of local unit and its leadership status.

#     Given a remote unit and the remote endpoint name.
#     """
#     local_data = get_unit_info(local_unit, model)
#     leader = local_data["leader"]

#     data = get_unit_info(remote_unit, model)
#     relation_info = data.get("relation-info")
#     if not relation_info:
#         raise RuntimeError(f"{remote_unit} has no relations")

#     raw_data = get_relation_by_endpoint(relation_info, local_endpoint, remote_endpoint, local_unit)
#     unit_data = raw_data["related-units"][local_unit]["data"]
#     app_data = raw_data["application-data"]
#     return unit_data, app_data, leader


# @dataclass
# class RelationData:
#     provider: UnitRelationData
#     requirer: UnitRelationData


# def get_relation_data(
#     *,
#     provider_endpoint: str,
#     requirer_endpoint: str,
#     include_default_juju_keys: bool = False,
#     model: str = None,
# ):
#     """Get relation databags for a juju relation.

#     >>> get_relation_data('prometheus/0:ingress', 'traefik/1:ingress-per-unit')
#     """
#     provider_data = get_content(
#         provider_endpoint, requirer_endpoint, include_default_juju_keys, model
#     )
#     requirer_data = get_content(
#         requirer_endpoint, provider_endpoint, include_default_juju_keys, model
#     )
#     return RelationData(provider=provider_data, requirer=requirer_data)


# async def deploy_literal_bundle(ops_test: OpsTest, bundle: str):
#     run_args = [
#         "juju",
#         "deploy",
#         "--trust",
#         "-m",
#         ops_test.model_name,
#         str(ops_test.render_bundle(bundle)),
#     ]

#     retcode, stdout, stderr = await ops_test.run(*run_args)
#     assert retcode == 0, f"Deploy failed: {(stderr or stdout).strip()}"
#     logger.info(stdout)


# async def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
#     cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
#     try:
#         res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
#         logger.info(res)
#     except subprocess.CalledProcessError as e:
#         logger.error(e.stdout.decode())
#         raise e
#     return res.stdout


# def present_facade(
#     interface: str,
#     app_data: Dict = None,
#     unit_data: Dict = None,
#     role: Literal["provide", "require"] = "provide",
#     model: str = None,
#     app: str = "facade",
# ):
#     """Set up the facade charm to present this data over the interface ``interface``."""
#     data = {
#         "endpoint": f"{role}-{interface}",
#     }
#     if app_data:
#         data["app_data"] = json.dumps(app_data)
#     if unit_data:
#         data["unit_data"] = json.dumps(unit_data)

#     with tempfile.NamedTemporaryFile(dir=os.getcwd()) as f:
#         fpath = Path(f.name)
#         fpath.write_text(yaml.safe_dump(data))

#         _model = f" --model {model}" if model else ""

#         subprocess.run(shlex.split(f"juju run {app}/0{_model} update --params {fpath.absolute()}"))


# async def get_unit_address(ops_test: OpsTest, app_name, unit_no):
#     status = await ops_test.model.get_status()
#     app = status["applications"][app_name]
#     if app is None:
#         assert False, f"no app exists with name {app_name}"
#     unit = app["units"].get(f"{app_name}/{unit_no}")
#     if unit is None:
#         assert False, f"no unit exists in app {app_name} with index {unit_no}"
#     return unit["address"]


# async def deploy_and_configure_minio(ops_test: OpsTest):
#     config = {
#         "access-key": ACCESS_KEY,
#         "secret-key": SECRET_KEY,
#     }
#     await ops_test.model.deploy(MINIO, channel="edge", trust=True, config=config)
#     await ops_test.model.wait_for_idle(apps=[MINIO], status="active", timeout=2000)
#     minio_addr = await get_unit_address(ops_test, MINIO, "0")

#     mc_client = Minio(
#         f"{minio_addr}:9000",
#         access_key="accesskey",
#         secret_key="secretkey",
#         secure=False,
#     )

#     # create tempo bucket
#     found = mc_client.bucket_exists(BUCKET_NAME)
#     if not found:
#         mc_client.make_bucket(BUCKET_NAME)

#     # configure s3-integrator
#     s3_integrator_app: Application = ops_test.model.applications[S3_INTEGRATOR]
#     s3_integrator_leader: Unit = s3_integrator_app.units[0]

#     await s3_integrator_app.set_config(
#         {
#             "endpoint": f"minio-0.minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
#             "bucket": BUCKET_NAME,
#         }
#     )

#     action = await s3_integrator_leader.run_action("sync-s3-credentials", **config)
#     action_result = await action.wait()
#     assert action_result.status == "completed"


# async def deploy_cluster(ops_test: OpsTest):
#     # await ops_test.model.deploy(FACADE, channel="edge")
#     # await ops_test.model.wait_for_idle(
#     #     apps=[FACADE], raise_on_blocked=True, status="active", timeout=2000
#     # )

#     # TODO: deploy from latest edge
#     mimir_worker_charm = "mimir-worker-k8s"

#     resources = {
#         "tempo-image": "docker.io/ubuntu/tempo:2-22.04",
#     }

#     await ops_test.model.deploy(
#         "mimir-worker-k8s-operator", application_name=WORKER_NAME, channel="latest/edge"
#     )
#     await ops_test.model.deploy(S3_INTEGRATOR, channel="edge")

#     await ops_test.model.integrate(APP_NAME + ":s3", S3_INTEGRATOR + ":s3-credentials")
#     await ops_test.model.integrate(APP_NAME + ":mimir-cluster", WORKER_NAME + ":mimir-cluster")

#     await deploy_and_configure_minio(ops_test)

#     await ops_test.model.wait_for_idle(
#         apps=[APP_NAME, WORKER_NAME, S3_INTEGRATOR],
#         status="active",
#         timeout=1000,
#         idle_period=30,
#     )
