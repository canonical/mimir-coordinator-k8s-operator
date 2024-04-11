# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Helper module for interacting with the Mimir configuration."""

import logging
import re
from dataclasses import asdict
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator, validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

S3_RELATION_NAME = "s3"
BUCKET_NAME = "mimir"

logger = logging.getLogger(__name__)


class InvalidConfigurationError(Exception):
    """Invalid configuration."""

    pass


class Memberlist(BaseModel):
    """Memberlist schema."""

    cluster_label: str
    cluster_label_verification_disabled: bool = False
    join_members: List[str]


class Tsdb(BaseModel):
    """Tsdb schema."""

    dir: str = "/data/ingester"


class BlocksStorage(BaseModel):
    """Blocks storage schema."""

    storage_prefix: str = "blocks"
    tsdb: Tsdb


class Limits(BaseModel):
    """Limits schema."""

    ingestion_rate: int = 0
    ingestion_burst_size: int = 0
    max_global_series_per_user: int = 0
    ruler_max_rules_per_rule_group: int = 0
    ruler_max_rule_groups_per_tenant: int = 0


class Kvstore(BaseModel):
    """Kvstore schema."""

    store: str = "memberlist"


class Ring(BaseModel):
    """Ring schema."""

    kvstore: Kvstore
    replication_factor: int = 3


class Distributor(BaseModel):
    """Distributor schema."""

    ring: Ring


class Ingester(BaseModel):
    """Ingester schema."""

    ring: Ring


class Ruler(BaseModel):
    """Ruler schema."""

    rule_path: str = "/data/ruler"
    alertmanager_url: Optional[str]


class Alertmanager(BaseModel):
    """Alertmanager schema."""

    data_dir: str = "/data/alertmanager"
    external_url: Optional[str]


class Server(BaseModel):
    """Server schema."""

    http_tls_config: Dict[str, Dict[str, str]]
    grpc_tls_config: Dict[str, Dict[str, str]]


class _S3ConfigData(BaseModel):
    model_config = {"populate_by_name": True}
    access_key_id: str = Field(alias="access-key")
    endpoint: str
    secret_access_key: str = Field(alias="secret-key")
    bucket_name: str = Field(alias="bucket")
    region: str = ""
    insecure: str = "false"

    @model_validator(mode="before")  # pyright: ignore
    @classmethod
    def set_insecure(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("endpoint", None):
            data["insecure"] = "true" if data["endpoint"].startswith("http://") else "false"
        return data

    @validator("endpoint")
    def remove_scheme(cls, v: str) -> str:
        """Remove the scheme from the s3 endpoint."""
        return re.sub(rf"^{urlparse(v).scheme}://", "", v)


class _FilesystemStorageBackend(BaseModel):
    dir: str


_StorageBackend = Union[_S3ConfigData, _FilesystemStorageBackend]
_StorageKey = Union[Literal["filesystem"], Literal["s3"]]


@pydantic_dataclass
class CommonConfig:
    """Common config schema."""

    backend: _StorageKey
    _StorageKey: _StorageBackend

    def __post_init__(self):
        """Verify the backend variable typing is correct."""
        if not asdict(self).get("s3", "") and not asdict(self).get("s3", ""):
            raise InvalidConfigurationError("Common storage configuration must specify a type!")
        elif (asdict(self).get("filesystem", "") and not self.backend != "filesystem") or (
            asdict(self).get("s3", "") and not self.backend != "s3"
        ):
            raise InvalidConfigurationError(
                "Mimir `backend` type must include a configuration block which matches that type"
            )


class MimirBaseConfig(BaseModel):
    """Base class for mimir config schema."""

    target: str
    memberlist: Memberlist
    multitenancy_enabled: bool = True
    common: CommonConfig
    limits: Limits
    blocks_storage: Optional[BlocksStorage]
    distributor: Optional[Distributor]
    ingester: Optional[Ingester]
    ruler: Optional[Ruler]
    alertmanager: Optional[Alertmanager]
    server: Optional[Server]
