import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger("obed")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)


def log_action(log_file: Path, action: str, price: float = None, tx_hash: str = None,
               fees_usd: float = None, **extra) -> None:
    """
    Пишет структурированную запись о действии бота: время, цена, действие,
    хэш транзакции, сумма комиссий — как требует спецификация.
    """
    record = {
        "ts": time.time(),
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "price": price,
        "tx_hash": tx_hash,
        "fees_usd": fees_usd,
    }
    record.update(extra)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("%s | price=%s tx=%s fees_usd=%s %s", action, price, tx_hash, fees_usd, extra)
