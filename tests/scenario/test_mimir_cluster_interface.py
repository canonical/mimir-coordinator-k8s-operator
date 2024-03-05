from itertools import chain

import ops
import pytest
from mimir_cluster import (
    MimirClusterProvider,
    MimirClusterRequirerAppData,
    MimirClusterRequirerUnitData,
    MimirRole,
)
from ops import Framework
from scenario import Context, Relation, State


class MyCharm(ops.CharmBase):
    META = {
        "name": "lukasz",
        "requires": {"mimir-cluster-require": {"interface": "mimir_cluster"}},
        "provides": {"mimir-cluster-provide": {"interface": "mimir_cluster"}},
    }

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.provider = MimirClusterProvider(self, endpoint="mimir-cluster-provide")


@pytest.mark.parametrize(
    "workers_roles, expected",
    (
        (
            (({MimirRole.overrides_exporter}, 1), ({MimirRole.overrides_exporter}, 1)),
            ({MimirRole.overrides_exporter: 2}),
        ),
        (
            (({MimirRole.query_frontend}, 1), ({MimirRole.overrides_exporter}, 1)),
            ({MimirRole.overrides_exporter: 1, MimirRole.query_frontend: 1}),
        ),
        ((({MimirRole.querier}, 2), ({MimirRole.querier}, 1)), ({MimirRole.querier: 3})),
        (
            (
                ({MimirRole.alertmanager}, 2),
                ({MimirRole.alertmanager}, 2),
                ({MimirRole.alertmanager, MimirRole.querier}, 1),
            ),
            ({MimirRole.alertmanager: 5, MimirRole.querier: 1}),
        ),
    ),
)
def test_role_collection(workers_roles, expected):
    relations = []
    for worker_roles, scale in workers_roles:
        data = MimirClusterRequirerAppData(roles=worker_roles).dump()
        relations.append(
            Relation(
                "mimir-cluster-provide",
                remote_app_data=data,
                remote_units_data={i: {} for i in range(scale)},
            )
        )

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_roles() == expected


@pytest.mark.parametrize(
    "workers_addresses",
    (
        (("https://foo.com", "http://bar.org:8001"), ("https://bar.baz",)),
        (("//foo.com", "http://bar.org:8001"), ("foo.org:5000/noz",)),
        (
            ("https://foo.com:1", "http://bar.org:8001", "ohmysod"),
            ("u.buntu", "red-hat-chili-pepperz"),
            ("hoo.kah",),
        ),
    ),
)
def test_address_collection(workers_addresses):
    relations = []
    topo = {"unit": "foo/0", "model": "bar"}
    remote_app_data = MimirClusterRequirerAppData(roles=[MimirRole.alertmanager]).dump()
    for worker_addresses in workers_addresses:
        units_data = {
            i: MimirClusterRequirerUnitData(address=address, juju_topology=topo).dump()
            for i, address in enumerate(worker_addresses)
        }
        relations.append(
            Relation(
                "mimir-cluster-provide",
                remote_units_data=units_data,
                remote_app_data=remote_app_data,
            )
        )

    # all unit addresses should show up
    expected = set(chain(*workers_addresses))

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_addresses() == expected
