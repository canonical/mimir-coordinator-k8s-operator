output "app_name" {
  value = juju_application.mimir_coordinator.name
}

output "provides" {
  value = {
    mimir_cluster = "mimir-cluster"
  }
}

output "requires" {
  value = {}
}
