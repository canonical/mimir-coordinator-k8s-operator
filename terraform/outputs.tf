output "app_name" {
  value = juju_application.mimir_coordinator.name
}

output "endpoints" {
  value = {
    mimir_cluster = "mimir-cluster"
  }
}
