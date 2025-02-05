"""TODO: Add a proper docstring here.

This is a placeholder docstring for this charm library. Docstrings are
presented on Charmhub and updated whenever you push a new version of the
library.

Complete documentation about creating and documenting libraries can be found
in the SDK docs at https://juju.is/docs/sdk/libraries.

See `charmcraft publish-lib` and `charmcraft fetch-lib` for details of how to
share and consume charm libraries. They serve to enhance collaboration
between charmers. Use a charmer's libraries for classes that handle
integration with their charm.

Bear in mind that new revisions of the different major API versions (v0, v1,
v2 etc) are maintained independently.  You can continue to update v0 and v1
after you have pushed v3.

Markdown is supported, following the CommonMark specification.
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
