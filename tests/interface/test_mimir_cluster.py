from interface_tester import InterfaceTester


def test_mimir_cluster_v0_interface(interface_tester: InterfaceTester):
    interface_tester.configure(
        interface_name="mimir_cluster",
        interface_version=0,
    )
    interface_tester.run()
