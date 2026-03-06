# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# TODO: Update once we have moved to a lib
"""OpenTelemetry protocol (OTLP) Library.

## Overview

This document explains how to integrate with the Opentelemetry-collector charm
for the purpose of providing OTLP telemetry to Opentelemetry-collector. This document is the
authoritative reference on the structure of relation data that is
shared between Opentelemetry-collector charms and any other charm that intends to
provide OTLP telemetry for Opentelemetry-collector.
"""

import copy
import json
import logging
from pathlib import Path
from typing import ClassVar, Dict, List, Literal, Optional, Sequence, Any

from cosl.juju_topology import JujuTopology
from cosl.rules import AlertRules, generic_alert_groups
from ops import CharmBase, Relation
from ops.framework import Object
from pydantic import BaseModel, ConfigDict, ValidationError

DEFAULT_CONSUMER_RELATION_NAME = "send-otlp"
DEFAULT_PROVIDER_RELATION_NAME = "receive-otlp"
DEFAULT_LOKI_ALERT_RULES_RELATIVE_PATH = "./src/loki_alert_rules"
DEFAULT_PROM_ALERT_RULES_RELATIVE_PATH = "./src/prometheus_alert_rules"


logger = logging.getLogger(__name__)


class InvalidAlertRulePathError(Exception):
    """Raised if the alert rules folder cannot be found or is otherwise invalid."""

    def __init__(
        self,
        alert_rules_absolute_path: Path,
        message: str,
    ):
        self.alert_rules_absolute_path = alert_rules_absolute_path
        self.message = message

        super().__init__(self.message)


class OtlpEndpoint(BaseModel):
    """A pydantic model for a single OTLP endpoint."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["http", "grpc"]
    endpoint: str
    telemetries: Sequence[Literal["logs", "metrics", "traces"]]


class Rules(BaseModel):
    """A pydantic model for a rules in different formats: logql, promql."""

    model_config = ConfigDict(extra="forbid")

    logql: Optional[Dict[str, Any]] = None
    promql: Optional[Dict[str, Any]] = None


class OtlpProviderAppData(BaseModel):
    """A pydantic model for the OTLP provider's unit databag."""

    KEY: ClassVar[str] = "otlp"

    model_config = ConfigDict(extra="forbid")

    endpoints: List[OtlpEndpoint]


class OtlpConsumerAppData(BaseModel):
    """A pydantic model for the OTLP provider's unit databag."""

    KEY: ClassVar[str] = "otlp"

    model_config = ConfigDict(extra="forbid")
    rules: Rules


class OtlpConsumer(Object):
    """A class for consuming OTLP endpoints."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_CONSUMER_RELATION_NAME,
        protocols: Optional[Sequence[Literal["http", "grpc"]]] = None,
        telemetries: Optional[Sequence[Literal["logs", "metrics", "traces"]]] = None,
        *,
        loki_alert_rules_path: str = DEFAULT_LOKI_ALERT_RULES_RELATIVE_PATH,
        prom_alert_rules_path: str = DEFAULT_PROM_ALERT_RULES_RELATIVE_PATH,
        forward_alert_rules: bool = True,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._protocols = protocols if protocols is not None else []
        self._telemetries = telemetries if telemetries is not None else []
        self._topology = JujuTopology.from_charm(charm)
        self._loki_alert_rules_path = self._validate_alert_rules_path(loki_alert_rules_path)
        self._prom_alert_rules_path = self._validate_alert_rules_path(prom_alert_rules_path)
        self._forward_alert_rules = forward_alert_rules

    def _validate_alert_rules_path(self, alert_rules_path: str) -> str:
        # TODO: Can we move this into cos-lib?
        try:
            alert_rules_path = self._resolve_dir_against_charm_path(alert_rules_path)
        except InvalidAlertRulePathError as e:
            logger.debug(
                "Invalid Prometheus alert rules folder at %s: %s",
                e.alert_rules_absolute_path,
                e.message,
            )
        return alert_rules_path

    def _resolve_dir_against_charm_path(self, *path_elements: str) -> str:
        """Resolve the provided path items against the directory of the main file.

        Look up the directory of the main .py file being executed. This is normally
        going to be the charm.py file of the charm including this library. Then, resolve
        the provided path elements and, if the result path exists and is a directory,
        return its absolute path; otherwise, return `None`.
        """
        charm_dir = Path(str(self._charm.charm_dir))
        alerts_dir_path = charm_dir.absolute().joinpath(*path_elements)
        if not alerts_dir_path.exists():
            raise InvalidAlertRulePathError(alerts_dir_path, "directory does not exist")
        if not alerts_dir_path.is_dir():
            raise InvalidAlertRulePathError(alerts_dir_path, "is not a directory")

        return str(alerts_dir_path)

    def _get_provider_databag(self, otlp_databag: str) -> Optional[OtlpProviderAppData]:
        """Load the OtlpProviderAppData from the given databag string.

        For each endpoint in the databag, if it contains unsupported telemetry types, those
        telemetries are filtered out before validation. If an endpoint contains an unsupported
        protocol, or has no supported telemetries, it is skipped entirely.
        """
        try:
            data = json.loads(otlp_databag)
            endpoints_data = data.get("endpoints", [])
        except json.JSONDecodeError as e:
            logger.error(f"Consumer failed validation of Provider's OTLP databag: {e}")
            return None

        valid_endpoints = []
        supported_telemetries = set(self._telemetries)
        for endpoint_data in endpoints_data:
            if filtered_telemetries := [
                t for t in endpoint_data.get("telemetries", []) if t in supported_telemetries
            ]:
                endpoint_data["telemetries"] = filtered_telemetries
            else:
                # If there are no supported telemetries for this endpoint, skip it entirely
                continue
            try:
                endpoint = OtlpEndpoint.model_validate(endpoint_data)
            except ValidationError:
                continue
            valid_endpoints.append(endpoint)
        try:
            return OtlpProviderAppData(endpoints=valid_endpoints)
        except ValidationError as e:
            logger.error(f"OTLP databag failed validation: {e}")
            return None

    @property
    def remote_otlp_endpoints(self) -> Dict[int, OtlpEndpoint]:
        """Return a mapping of relation ID to OTLP endpoint.

        For each remote unit's list of OtlpEndpoints:
            - If a telemetry type is not supported, then the endpoint is accepted, but the
              telemetry is ignored.
            - If the endpoint contains an unsupported protocol it is ignored.
            - The first available (and supported) endpoint is returned.
        """
        endpoints = {}
        for rel in self.model.relations[self._relation_name]:
            if not (otlp := rel.data[rel.app].get(OtlpProviderAppData.KEY)):
                continue
            if not (app_databag := self._get_provider_databag(otlp)):
                continue

            # Choose the first valid endpoint in list
            if endpoint_choice := next(
                (e for e in app_databag.endpoints if e.protocol in self._protocols), None
            ):
                endpoints[rel.id] = endpoint_choice

        return endpoints

    def publish(self, relation: Optional[Relation] = None):
        """Triggers programmatically the update of the relation data.

        Args:
            relation: An optional instance of `class:ops.model.Relation` to update.
                If not provided, all instances of the `otlp`
                relation are updated.

        There are 2 rules file paths which are loaded from disk and published to the databag. The
        rules files exist in 2 separate directories, distinguished by logql and promql expression
        formats.
        """
        if not self._charm.unit.is_leader():
            return

        relations = [relation] if relation else self.model.relations[self._relation_name]

        rules = None
        loki_alert_rules = AlertRules(query_type="logql", topology=self._topology)
        prom_alert_rules = AlertRules(query_type="promql", topology=self._topology)
        # TODO: is this the correct place for generic alert rules?
        prom_alert_rules.add(
            copy.deepcopy(generic_alert_groups.application_rules),
            group_name_prefix=self._topology.identifier,
        )
        if self._forward_alert_rules:
            loki_alert_rules.add_path(self._loki_alert_rules_path, recursive=True)
            prom_alert_rules.add_path(self._prom_alert_rules_path, recursive=True)
            rules = Rules.model_validate(
                {"logql": loki_alert_rules.as_dict(), "promql": prom_alert_rules.as_dict()}
            )

        if rules is None:
            return

        for relation in relations:
            relation.data[self._charm.app]["rules"] = rules.model_dump_json()


class OtlpProvider(Object):
    """A class for publishing all supported OTLP endpoints.

    Args:
        charm: The charm instance.
        protocol_ports: A dictionary mapping ProtocolType to port number.
        relation_name: The name of the relation to use.
        path: An optional path to append to the endpoint URLs.
        supported_telemetries: A list of supported telemetry types.
    """

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_PROVIDER_RELATION_NAME,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._endpoints = []

    def add_endpoint(
        self,
        protocol: Literal["http", "grpc"],
        endpoint: str,
        telemetries: Sequence[Literal["logs", "metrics", "traces"]],
    ):
        """Add an OtlpEndpoint to the list.

        Call this method after endpoint-changing events e.g. TLS and ingress.
        """
        self._endpoints.append(
            OtlpEndpoint(protocol=protocol, endpoint=endpoint, telemetries=telemetries)
        )

    def publish(self, relation: Optional[Relation] = None) -> None:
        """Triggers programmatically the update of the relation data.

        Args:
            relation: An optional instance of `class:ops.model.Relation` to update.
                If not provided, all instances of the `otlp`
                relation are updated.
        """
        if not self._charm.unit.is_leader():
            # Only the leader unit can write to app data.
            return

        relations = [relation] if relation else self.model.relations[self._relation_name]
        for relation in relations:
            data = OtlpProviderAppData(endpoints=self._endpoints).model_dump(exclude_none=True)
            otlp = {OtlpProviderAppData.KEY: data}
            relation.data[self._charm.app].update({k: json.dumps(v) for k, v in otlp.items()})

    def _get_consumer_databag(self, otlp_databag: str) -> Optional[OtlpConsumerAppData]:
        """Load the OtlpConsumerAppData from the given databag string."""
        try:
            return OtlpConsumerAppData.model_validate(json.loads(otlp_databag))
        except ValidationError as e:
            logger.error(f"Provider failed validation of Consumer's OTLP databag: {e}")
            return None

    def _get_rules(self, format: Literal["logql", "promql"]) -> Dict[str, Any]:
        """Combine the rules groups into one list for all relations.

        Returns:
            A dict following the official rule format: {'groups': [[...]]}
        """
        rules = {}
        for rel in self.model.relations[self._relation_name]:
            if not (otlp := rel.data[rel.app].get(OtlpConsumerAppData.KEY)):
                continue
            if not (app_databag := self._get_consumer_databag(otlp)):
                continue

            databag_rules = app_databag.rules.logql if format == "logql" else app_databag.rules.promql
            if not databag_rules:
                return rules

            rules.setdefault("groups", []).extend(databag_rules.get("groups", []))

        return rules

    @property
    def remote_logql_rules(self):
        """Return alert rules (official rule format), containing only logql rules."""
        return self._get_rules(format="logql")

    @property
    def remote_promql_rules(self):
        """Return alert rules (official rule format), containing only promql rules."""
        return self._get_rules(format="promql")
