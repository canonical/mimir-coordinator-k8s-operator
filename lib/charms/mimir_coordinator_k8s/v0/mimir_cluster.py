"""This file defines the schemas for the provider and requirer sides of this relation interface.

It must expose two interfaces.schema_base.DataBagSchema subclasses called:
- ProviderSchema
- RequirerSchema

TODO: see https://github.com/canonical/charm-relation-interfaces/issues/121
"""
from enum import Enum
from typing import Dict, List
from typing import Optional

import pydantic
from pydantic import BaseModel
from pydantic import Json

LIBID = "9818a8d44028454a94c6c3a01f4316d2"

LIBAPI = 0
LIBPATCH = 0


class DataBagSchema(BaseModel):
    """Base class for relation interface databag schemas.

    Subclass from this base class and override "unit" and/or "app" to create a specification for
    a databag schema.

    For example:

    >>> from pydantic import Json
    >>>
    >>> class MyUnitConsumerSchema(DataBagSchema):
    >>>     foo: Json[int]
    >>>     bar: str
    >>>
    >>> # this class needs to be named "ConsumerSchema"
    >>> # for it to be picked up by the automated tester.
    >>> class ConsumerSchema(DataBagSchema):
    >>>     unit: MyUnitConsumerSchema

    This specifies that for a relation to satisfy MyRequirerSchema, the application databag
    needs to be empty and the unit databag needs to contain exactly a "bar":string and a
    "foo":Json-encoded int value.

    By using pydantic's validator API, you can specify further constraints on the values,
    provide defaults, enforce encoding/decoding, and more.
    """
    unit: Optional[BaseModel] = None
    app: Optional[BaseModel] = None


class MimirRole(str, Enum):
    """Mimir component role names."""
    overrides_exporter = "overrides_exporter"
    query_scheduler = "query_scheduler"
    flusher = "flusher"
    query_frontend = "query_frontend"
    querier = "querier"
    store_gateway = "store_gateway"
    ingester = "ingester"
    distributor = "distributor"
    ruler = "ruler"
    alertmanager = "alertmanager"
    compactor = "compactor"


class S3Config(pydantic.BaseModel):
    url: str
    endpoint: str
    secret_key: str
    access_key: str
    insecure: bool


class MyProviderAppDataBag(pydantic.BaseModel):
    hash_ring: Json[List[str]]
    s3_config: Json[S3Config]
    config: Json[Dict[str, str]]


class ProviderSchema(DataBagSchema):
    """The schema for the provider side of this interface."""
    app: MyProviderAppDataBag


class JujuTopology(pydantic.BaseModel):
    model: str
    unit: str
    # ...


class MyRequirerUnitDataBag(pydantic.BaseModel):
    juju_topology: Json[JujuTopology]


class MyRequirerAppDataBag(pydantic.BaseModel):
    roles: List[MimirRole]


class RequirerSchema(DataBagSchema):
    """The schema for the requirer side of this interface."""
    unit: MyRequirerUnitDataBag
    app: MyRequirerAppDataBag
