# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import scenario
from coordinated_workers.nginx import Nginx
from helpers import get_relation_data
from scenario import Relation, State

from charm import NGINX_PORT, NGINX_TLS_PORT


def test_ingress_tls(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN Loki is related over the ingress and certificates endpoints
    ingress = Relation("ingress")
    certificates = Relation("certificates")

    state_in = State(
        relations=[
            s3,
            all_worker,
            ingress,
            certificates,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
    )

    # WHEN TLS is not yet available
    with context(context.on.relation_joined(ingress), state_in) as mgr:
        charm = mgr.charm
        state_out = mgr.run()

        # THEN there are no certificates on disk
        assert not charm.coordinator.nginx.are_certificates_on_disk

        # AND Loki publishes its Nginx non-TLS port in the ingress databag
        assert get_relation_data(state_out.relations, "ingress", "port") == str(NGINX_PORT)

    # AND WHEN TLS is enabled
    with patch.object(Nginx, "are_certificates_on_disk", return_value=True):
        # AND the ingress databag is updated
        state_out = context.run(context.on.relation_changed(ingress), state_in)

        # THEN Loki publishes its Nginx TLS port in the ingress databag
        assert get_relation_data(state_out.relations, "ingress", "scheme") == '"https"'
        assert get_relation_data(state_out.relations, "ingress", "port") == str(NGINX_TLS_PORT)
