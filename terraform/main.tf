resource "juju_application" "mimir_coordinator" {
  name = var.app_name
  # Coordinator and worker must be in the same model
  model = var.model_name
  units  = var.units
  config = var.config
  constraints = var.constraints

  charm {
    name     = "mimir-coordinator-k8s"
    channel  = var.channel
    revision = var.revision
  }
}