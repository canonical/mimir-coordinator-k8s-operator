variable "app_name" {
  description = "Application name"
  type        = string
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "latest/stable"
}

variable "config" {
  description = "Config options as in the ones we pass in juju config"
  type        = map(string)
  default     = {}
}

# We use constraints to set AntiAffinity in K8s
# https://discourse.charmhub.io/t/pod-priority-and-affinity-in-juju-charms/4091/13?u=jose
variable "constraints" {
  description = "Constraints to be applied"
  type        = string
  default     = ""
}

variable "model_name" {
  description = "Model name"
  type        = string
}

variable "revision" {
  description = "Revision number of the charm"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units"
  type        = number
  default     = 1
}

# We use constraints to set AntiAffinity in K8s
# https://discourse.charmhub.io/t/pod-priority-and-affinity-in-juju-charms/4091/13?u=jose
variable "constraints" {
  description = "String listing constraints for this application"
  type        = string
  # FIXME: Passing an empty constraints value to the Juju Terraform provider currently
  # causes the operation to fail due to https://github.com/juju/terraform-provider-juju/issues/344
  default = "arch=amd64"
}