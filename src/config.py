import os
import yaml
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict

    @property
    def chain_id(self) -> int:
        return self.raw["network"]["chain_id"]

    @property
    def rpc_urls(self) -> list:
        urls = [self.raw["network"]["rpc_url"]]
        urls += self.raw["network"].get("rpc_fallback_urls", [])
        return urls

    @property
    def factory(self) -> str:
        return self.raw["contracts"]["factory"]

    @property
    def position_manager(self) -> str:
        return self.raw["contracts"]["position_manager"]

    @property
    def swap_router02(self) -> str:
        return self.raw["contracts"]["swap_router02"]

    @property
    def wbnb(self) -> str:
        return self.raw["contracts"]["wbnb"]

    @property
    def pool_address(self) -> str:
        addr = self.raw["pool"]["address"]
        if not addr:
            raise ValueError("pool.address не заполнен в config.yaml")
        return addr

    @property
    def pool_token0(self) -> str:
        return self.raw["pool"]["token0"]

    @property
    def pool_token1(self) -> str:
        return self.raw["pool"]["token1"]

    @property
    def fee_tier(self) -> int:
        return self.raw["pool"]["fee_tier"]

    @property
    def range_width_pct(self) -> float:
        return self.raw["position"]["range_width_pct"]

    @property
    def check_interval_hours(self) -> float:
        return self.raw["position"]["check_interval_hours"]

    @property
    def slippage_bps(self) -> int:
        return self.raw["position"]["slippage_bps"]

    @property
    def fee_threshold_payout(self) -> float:
        return self.raw["fees"]["threshold_payout_token"]

    @property
    def payout_token_address(self) -> str:
        return self.raw["fees"]["payout_token_address"]

    @property
    def withdrawal_address(self) -> str:
        addr = self.raw["fees"]["withdrawal_address"]
        if not addr:
            raise ValueError("fees.withdrawal_address не заполнен в config.yaml")
        return addr

    @property
    def state_file(self) -> Path:
        return ROOT / self.raw["paths"]["state_file"]

    @property
    def log_file(self) -> Path:
        return ROOT / self.raw["paths"]["log_file"]

    @property
    def private_key(self) -> str:
        key = os.environ.get("BOT_PRIVATE_KEY")
        if not key:
            raise ValueError("BOT_PRIVATE_KEY не задан в переменных окружения")
        if not key.startswith("0x"):
            key = "0x" + key
        return key


def load_config(path: str = None) -> Config:
    path = path or str(ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(raw=raw)
