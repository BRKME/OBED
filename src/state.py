import json
import time
from pathlib import Path
from typing import Optional


DEFAULT_STATE = {
    "position": None,           # {"token_id": int, "tick_lower": int, "tick_upper": int}
    "last_check_ts": None,      # unix timestamp последней проверки
    "schema_version": 1,
}


def load_state(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_STATE)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # подстраховка на случай если файл был создан вручную и в нём не все ключи
    for k, v in DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def seconds_since_last_check(state: dict) -> Optional[float]:
    if state.get("last_check_ts") is None:
        return None
    return time.time() - state["last_check_ts"]


def mark_checked_now(state: dict) -> None:
    state["last_check_ts"] = time.time()
