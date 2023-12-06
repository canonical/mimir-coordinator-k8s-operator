import pytest
from interface_tester import InterfaceTester
from charm import MimirCoordinatorK8SOperatorCharm


@pytest.fixture
def interface_tester(interface_tester: InterfaceTester):

    # TODO: add patches and possibly state_template if needed
    # with patch(...)

    interface_tester.configure(
        charm_type=MimirCoordinatorK8SOperatorCharm,
        interface_name="mimir_cluster",
        branch="mimir-cluster-interface"
    )

    yield interface_tester
