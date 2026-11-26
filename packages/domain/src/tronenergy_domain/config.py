from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TE_", env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql://tronenergy:local-only@localhost:5432/tronenergy")
    redis_url: str = "redis://localhost:6379/0"
    network: Literal["nile", "shasta", "mainnet"] = "nile"
    rpc_url: str = "https://nile.trongrid.io"
    rpc_api_key: SecretStr = SecretStr("")
    # The expected genesis block binds the configured RPC to an operator-verified network.
    genesis_block_id: str = ""
    api_keys: dict[str, str] = {}
    admin_keys: list[str] = []
    internal_token: SecretStr = SecretStr("")
    signer_token: SecretStr = SecretStr("")
    chain_url: str = "http://localhost:8002"
    rental_url: str = "http://localhost:8001"
    signer_url: str = "http://localhost:8003"
    private_key_file: str = ""
    signing_enabled: bool = False
    broadcasting_enabled: bool = False
    max_stake_sun: int = 100_000_000_000
    permission_id: int = 0
    token_contracts: list[str] = []
    duration_options: list[int] = [3600, 86400]
    price_per_million_unit_hour_sun: int = 1_000_000
    quote_ttl_seconds: int = 30
    snapshot_ttl_seconds: int = 15
    rpc_timeout: float = 10
    poll_seconds: float = 3
    rate_per_minute: int = 120
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.network == "mainnet" and (self.signing_enabled or self.broadcasting_enabled):
            raise ValueError("V1 mainnet execution is disabled until testnet acceptance")
        if self.price_per_million_unit_hour_sun <= 0 or self.quote_ttl_seconds <= 0:
            raise ValueError("price and quote TTL must be positive")
        if not self.duration_options or any(x <= 0 for x in self.duration_options):
            raise ValueError("duration options must be positive")
        return self


@lru_cache
def settings():
    return Settings()
