"""
## Prometheus API Library

This library facilitates communicating info about the prometheus api to a remote charm.

### Requirer Usage

```
self.prometheus_api = PrometheusApiRequirer(charm=self)

...

prometheus_ingress_url = self.prometheus_api.get_data.ingress_url
prometheus_internal_url = self.prometheus_api.get_data.internal_url

### Provider Usage

```
PrometheusApiProvider(
    charm=self,
    ingress_url=self.external_url,
    internal_url=self.internal_url,
    refresh_event=[
        self.ingress.on.ready,
    ],
)
```
"""

from typing import List, Optional, Union

from charm_relation_building_blocks.relation_handlers import Receiver, Sender
from ops import BoundEvent, CharmBase
from pydantic import BaseModel, Field

# The unique Charmhub library identifier, never change it
LIBID = "bf718724761b4371ab028921f72be244"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class PrometheusApiAppData(BaseModel):
    """Data model for the prometheus_api interface."""

    ingress_url: str = Field(description="The ingress URL.")
    internal_url: str = Field(description="The URL for connecting to the prometheus api from inside the cluster.")


class PrometheusApiRequirer(Receiver):
    """Class for handling the receiver side of the prometheus_api relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        super().__init__(charm, relation_name, PrometheusApiAppData, refresh_event)


class PrometheusApiProvider(Sender):
    """Class for handling the sender side of the prometheus_api relation."""

    def __init__(
        self,
        charm: CharmBase,
        ingress_url: str,
        internal_url: str,
        relation_name: str = "prometheus-api",
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        data = PrometheusApiAppData(ingress_url=ingress_url, internal_url=internal_url)
        super().__init__(charm, data, relation_name, refresh_event)
