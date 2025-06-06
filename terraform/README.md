# Terraform module for mimir-coordinator-k8s


This is a Terraform module facilitating the deployment of mimir-coordinator-k8s charm, using the [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs).


## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

## API

### Inputs
The module offers the following configurable inputs:

| Name | Type | Description | Required |
| - | - | - | - |
| `app_name`| string | Application name | mimir |
| `channel`| string | Channel that the charm is deployed from | latest/edge |
| `config`| map(any) | Map of the charm configuration options | {} |
| `constraints`| string | Constraints for the Juju deployment | "" |
| `model_name`| string | Name of the model that the charm is deployed on |  |
| `revision`| number | Revision number of the charm name |  |
| `units`| number | Number of units to deploy | 1 |
| `constraints`| string | String listing constraints for this application | arch=amd64 |

### Outputs
Upon applied, the module exports the following outputs:

| Name | Description |
| - | - |
| `app_name`|  Application name |
| `requires`|  Map of `requires` endpoints |

## Usage

> [!NOTE]
> This module is intended to be used only in conjunction with its counterpart, [Mimir worker module](https://github.com/canonical/mimir-worker-k8s-operator) and, when deployed in isolation, is not functional.
> For the Mimir HA solution module deployment, check [Mimir HA module](https://github.com/canonical/observability)


Users should ensure that Terraform is aware of the `juju_model` dependency of the charm module.

To deploy this module with its needed dependency, you can run `terraform apply -var="model_name=<MODEL_NAME>" -auto-approve`
