output "app_name" {
  value = juju_application.mimir_coordinator.name
}

output "endpoints" {
  value = {
    # Requires
    # Provides
    mimir_cluster = "mimir-cluster"
  }
}
