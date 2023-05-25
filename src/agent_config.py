"""Grafana agent config builder."""

from typing import Any, Dict, List, Optional

from charms.observability_libs.v0.juju_topology import JujuTopology


class Config:
    """A 'config builder' for grafana agent."""

    def __init__(
        self,
        *,
        topology: JujuTopology,
        scrape_configs: Optional[list] = None,
        remote_write: Optional[List[Dict[str, Any]]] = None,
        loki_endpoints: Optional[List[dict]] = None,
        positions_dir: str = "/run",
        insecure_skip_verify: bool = False,
        http_listen_port: int = 3500,
        grpc_listen_port: int = 3600,
    ):
        self._topology = topology

        scrape_configs = (scrape_configs or []).copy()
        remote_write = (remote_write or []).copy()
        loki_endpoints = (loki_endpoints or []).copy()

        for endpoint in remote_write + loki_endpoints:
            endpoint["tls_config"] = {"insecure_skip_verify": insecure_skip_verify}

        self._config = {
            "server": {"log_level": "info"},
            "integrations": self._integrations_config(remote_write),
            "metrics": {
                "wal_directory": "/tmp/agent/data",  # should match metadata
                "configs": [
                    {
                        "name": "agent_scraper",
                        "scrape_configs": scrape_configs,
                        "remote_write": remote_write,
                    }
                ],
            },
            "logs": {
                "positions_directory": f"{positions_dir.rstrip('/')}/grafana-agent-positions",
                "configs": [
                    {
                        "name": "push_api_server",
                        "clients": loki_endpoints,
                        "scrape_configs": [
                            {
                                "job_name": "loki",
                                "loki_push_api": {
                                    "server": {
                                        "http_listen_port": http_listen_port,
                                        "grpc_listen_port": grpc_listen_port,
                                    },
                                },
                            }
                        ],
                    }
                ],  # TODO: capture `_additional_log_configs` logic for the machine charm
            },
        }

        # Seems like we cannot have an empty "configs" section. Delete it if no endpoints.
        if not loki_endpoints:
            self._config["logs"] = {}

    def _instance_name(self) -> str:
        parts = [
            self._topology.model,
            self._topology.model_uuid,
            self._topology.application,
            self._topology.unit,
        ]
        return "_".join(parts)  # TODO do we also need to `replace("/", "_")` ?

    def _integrations_config(self, remote_write) -> dict:
        """Return the integrations section of the config.

        Returns:
            The dict representing the config
        """
        # Align the "job" name with those of prometheus_scrape
        job_name = "juju_{}_{}_{}_self-monitoring".format(
            self._topology.model, self._topology.model_uuid, self._topology.application
        )

        conf = {
            "agent": {
                "enabled": True,
                "relabel_configs": [
                    {
                        "target_label": "job",
                        "regex": "(.*)",
                        "replacement": job_name,
                    },
                    {  # Align the "instance" label with the rest of the Juju-collected metrics
                        "target_label": "instance",
                        "regex": "(.*)",
                        "replacement": self._instance_name,
                    },
                    {  # To add a label, we create a relabelling that replaces a built-in
                        "source_labels": ["__address__"],
                        "target_label": "juju_charm",
                        "replacement": self._topology.charm_name,
                    },
                    {  # To add a label, we create a relabelling that replaces a built-in
                        "source_labels": ["__address__"],
                        "target_label": "juju_model",
                        "replacement": self._topology.model,
                    },
                    {
                        "source_labels": ["__address__"],
                        "target_label": "juju_model_uuid",
                        "replacement": self._topology.model_uuid,
                    },
                    {
                        "source_labels": ["__address__"],
                        "target_label": "juju_application",
                        "replacement": self._topology.application,
                    },
                    {
                        "source_labels": ["__address__"],
                        "target_label": "juju_unit",
                        "replacement": self._topology.unit,
                    },
                ],
            },
            "prometheus_remote_write": remote_write,
            # TODO capture `_additional_integrations` logic for the machine charm
        }
        return conf
