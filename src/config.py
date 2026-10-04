import os
import yaml
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict

    @property
    def name(self) -> str:
        return self.raw.get("name", "bsc")

    @property
    def enabled(self) -> bool:
        """Выключенный инстанс не трогает сеть и не требует ключа."""
        return bool(self.raw.get("enabled", True))

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
    def wrapped_native(self) -> str:
        """Обёртка нативной монеты (WBNB / WETH). Старый ключ wbnb — для BSC-конфига."""
        c = self.raw["contracts"]
        return c.get("wrapped_native") or c.get("wbnb") or ""

    @property
    def min_gas_native(self) -> float:
        """Минимум нативной монеты на газ полного цикла close->swap->mint."""
        return float(self.raw["position"].get("min_gas_native", 0.0003))

    @property
    def unit_label(self) -> str:
        """Как подписывать token1 в отчёте LP против HODL."""
        return self.raw.get("stats", {}).get("unit_label", "token1")

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
    def private_key_env(self) -> str:
        """Имя переменной окружения (секрета Actions) с ключом этого инстанса."""
        return self.raw.get("secrets", {}).get("private_key_env", "BOT_PRIVATE_KEY")

    @property
    def private_key(self) -> str:
        key = os.environ.get(self.private_key_env)
        if not key:
            raise ValueError(f"{self.private_key_env} не задан в переменных окружения")
        if not key.startswith("0x"):
            key = "0x" + key
        return key


def load_config(path: str = None) -> Config:
    """Конфиг инстанса: явный путь, иначе OBED_CONFIG, иначе config.yaml (BSC)."""
    path = path or os.environ.get("OBED_CONFIG") or "config.yaml"
    path = str(ROOT / path)   # абсолютный путь ROOT не меняет
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(raw=raw)
