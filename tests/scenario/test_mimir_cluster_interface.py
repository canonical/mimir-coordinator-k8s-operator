from itertools import chain

import ops
import pytest
from ops import Framework
from scenario import Relation, State, Context

from charms.mimir_coordinator_k8s.v0.mimir_cluster import MimirRole, MimirClusterRequirerAppData, MimirClusterRequirer, \
    MimirClusterProvider, MimirClusterRequirerUnitData, MimirClusterProviderAppData, DatabagAccessPermissionError


class MyCharm(ops.CharmBase):
    META = {"name": "lukasz",
            "requires": {"mimir-cluster-require": {"interface": "mimir_cluster"}},
            "provides": {"mimir-cluster-provide": {"interface": "mimir_cluster"}},
            }

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.requirer = MimirClusterRequirer(self, endpoint='mimir-cluster-require')
        self.provider = MimirClusterProvider(self, endpoint='mimir-cluster-provide')


@pytest.mark.parametrize("workers_roles, expected", (
        (
                ({MimirRole.overrides_exporter: 1},
                 {MimirRole.overrides_exporter: 1}),
                ({MimirRole.overrides_exporter: 2})
        ),
        (
                ({MimirRole.query_frontend: 1},
                 {MimirRole.overrides_exporter: 1}),
                ({MimirRole.overrides_exporter: 1,
                  MimirRole.query_frontend: 1})
        ),
        (
                ({MimirRole.querier: 2},
                 {MimirRole.querier: 1}),
                ({MimirRole.querier: 3})
        ),
        (
                ({MimirRole.alertmanager: 2}, {MimirRole.alertmanager: 2},
                 {MimirRole.alertmanager: 1, MimirRole.querier: 1}),
                ({MimirRole.alertmanager: 5, MimirRole.querier: 1})
        ),

))
def test_role_collection(workers_roles, expected):
    relations = []
    for worker_roles in workers_roles:
        data = MimirClusterRequirerAppData(roles=worker_roles).dump()
        relations.append(Relation("mimir-cluster-provide", remote_app_data=data))

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_roles() == expected


@pytest.mark.parametrize("workers_addresses", (
        (("https://foo.com", "http://bar.org:8001"),
         ("https://bar.baz",)),
        (("//foo.com", "http://bar.org:8001"),
         ("foo.org:5000/noz",)),
        (("https://foo.com:1", "http://bar.org:8001", "ohmysod"),
         ("u.buntu", "red-hat-chili-pepperz"),
         ("hoo.kah",)),
))
def test_address_collection(workers_addresses):
    relations = []
    topo = {'unit': 'foo/0',
            'model': 'bar'}
    for worker_addresses in workers_addresses:
        units_data = {i: MimirClusterRequirerUnitData(
            address=address,
            juju_topology=topo
        ).dump() for i, address in enumerate(worker_addresses)}
        relations.append(Relation("mimir-cluster-provide", remote_units_data=units_data))

    # all unit addresses should show up
    expected = set(chain(*workers_addresses))

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_addresses() == expected


def test_requirer_getters():
    cfg = {"a": "b"}
    relation = Relation("mimir-cluster-require",
                        remote_app_data=MimirClusterProviderAppData(mimir_config=cfg).dump())

    state = State(relations=[relation])

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.requirer.get_mimir_config() == cfg


@pytest.mark.parametrize("roles, address, valid", (
        ({MimirRole.querier:3, MimirRole.alertmanager:2}, "http://foo.com:24/bar", True),
        ({MimirRole.querier:1}, "https://foo.com", True),
        ({"boo": 1}, "https://foo.com", False),
        ([MimirRole.querier], "https://foo.com", False),
        (["coo"], "https://foo.com", False),
        ({MimirRole.querier:1}, "//foo.com", False),
))
def test_requirer_setters_leader(roles, address, valid):
    relation = Relation("mimir-cluster-require")

    state = State(relations=[relation], leader=True)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        try:
            charm.requirer.publish_app_roles(roles)
            charm.requirer.publish_unit_address(address)
        except Exception as e:
            if valid:
                raise


def test_requirer_setters_follower():
    relation = Relation("mimir-cluster-require")
    state = State(relations=[relation])

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm

        with pytest.raises(DatabagAccessPermissionError):
            charm.requirer.publish_app_roles({MimirRole.querier:2})
