output "app_name" {
  value = juju_application.mimir_coordinator.name
}

output "requires" {
  value = {
    mimir_cluster = "mimir-cluster"
  }
}
