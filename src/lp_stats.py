"""
Статистика LP: обгоняет ли позиция простое удержание токенов (HODL).

Источник — записи `snapshot` в actions.jsonl, которые бот пишет в конце каждого
тика (см. position_manager.take_snapshot). Всё считается в token1 (WBNB).

  LP   = всё, что сейчас есть у бота (позиция + невыведенные комиссии + свободный
         баланс токенов) + выведенные на обед комиссии − потраченный газ
  HODL = токены первого снимка, оценённые по текущей цене

Разница LP − HODL — это комиссии минус IL минус издержки переоткрытий (свопы
через пул, проскальзывание) минус газ. Ответ на вопрос «стоит ли масштабировать».

Если оператор доливает или забирает капитал, база ломается — запусти с
`--since <unix ts>` после последнего пополнения.

    python -m src.lp_stats [--since TS]
"""
import argparse
import json
import time
from pathlib import Path
from typing import Optional

MIN_DAYS_TO_JUDGE = 30
STALE_HOURS = 12       # тик раз в 4 ч: 12 ч без снимка — уже не случайность
PAYOUT_DECIMALS = 18   # payout — WBNB (token1), 18 знаков


def _holdings(s: dict) -> tuple:
    return (s["free0"] + s["pos0"] + s["fee0"],
            s["free1"] + s["pos1"] + s["fee1"])


def compute_report(records: list, since_ts: Optional[float] = None) -> Optional[dict]:
    since_ts = since_ts if since_ts is not None else float("-inf")
    snaps = [r for r in records if r.get("action") == "snapshot" and r["ts"] >= since_ts]
    if not snaps:
        return None
    first, last = snaps[0], snaps[-1]
    t0, t1 = first["ts"], last["ts"]

    h0, h1 = _holdings(first)
    n0, n1 = _holdings(last)

    in_window = [r for r in records if t0 < r["ts"] <= t1]
    withdrawn = sum(r.get("amount_payout") or 0 for r in in_window
                    if r.get("action") == "withdraw_fees") / 10 ** PAYOUT_DECIMALS
    reopens = sum(1 for r in in_window if r.get("action") == "close_position")
    # ошибки снимков считаем и после последнего снимка — там они важнее всего
    snapshot_errors = sum(1 for r in records
                          if r.get("action") == "snapshot_error" and r["ts"] >= t0)

    # газ — только падения нативного баланса между снимками; рост — это пополнение
    # оператором, а не доход позиции
    gas = sum(max(0.0, a["native"] - b["native"]) for a, b in zip(snaps, snaps[1:]))

    start_value = h0 * first["price"] + h1
    equity = n0 * last["price"] + n1
    lp_value = equity + withdrawn - gas
    hodl_value = h0 * last["price"] + h1
    diff = lp_value - hodl_value

    return {
        "since_ts": t0,
        "until_ts": t1,
        "days": (t1 - t0) / 86400,
        "snapshots": len(snaps),
        "price_start": first["price"],
        "price_now": last["price"],
        "start_value": start_value,
        "equity": equity,
        "withdrawn": withdrawn,
        "gas": gas,
        "lp_value": lp_value,
        "hodl_value": hodl_value,
        "diff": diff,
        "diff_pct": diff / hodl_value * 100 if hodl_value else 0.0,
        "reopens": reopens,
        "snapshot_errors": snapshot_errors,
    }


def render_report(rep: Optional[dict], now_ts: Optional[float] = None) -> str:
    if rep is None:
        return "### LP против HODL\n\nСнимков ещё нет — статистика начнётся со следующего тика."

    sign = "+" if rep["diff"] >= 0 else ""
    verdict = "LP обгоняет HODL" if rep["diff"] >= 0 else "LP отстаёт от HODL"
    lines = [
        "### LP против HODL",
        "",
        f"Период: {rep['days']:.1f} дн., снимков {rep['snapshots']}, "
        f"переоткрытий {rep['reopens']}",
        f"Цена token1/token0: {rep['price_start']:.6f} → {rep['price_now']:.6f}",
        "",
        "| | WBNB |",
        "|---|---|",
        f"| на старте | {rep['start_value']:.4f} |",
        f"| у бота сейчас | {rep['equity']:.4f} |",
        f"| выведено на обед | +{rep['withdrawn']:.4f} |",
        f"| газ | −{rep['gas']:.4f} |",
        f"| **LP итого** | **{rep['lp_value']:.4f}** |",
        f"| **HODL** | **{rep['hodl_value']:.4f}** |",
        "",
        f"**{verdict}: {sign}{rep['diff']:.4f} WBNB ({sign}{rep['diff_pct']:.2f}%)**",
    ]
    # Тихий отказ хуже громкого: если снимки перестали писаться, отчёт должен
    # сказать об этом сам, а не показывать старые цифры как текущие.
    if now_ts is not None and now_ts - rep["until_ts"] > STALE_HOURS * 3600:
        age_h = (now_ts - rep["until_ts"]) / 3600
        lines[1:1] = ["", f"⚠️ **Статистика не обновлялась {age_h:.0f} ч** — "
                          f"снимки перестали писаться, цифры ниже устарели."]
    if rep.get("snapshot_errors"):
        lines += ["", f"⚠️ {rep['snapshot_errors']} раз снимок не записался — "
                      f"см. `snapshot_error` в actions.jsonl."]
    if rep["days"] < MIN_DAYS_TO_JUDGE:
        lines += ["", f"_Меньше {MIN_DAYS_TO_JUDGE} дней — рано судить: "
                      f"одно переоткрытие может перевернуть знак._"]
    return "\n".join(lines)


def _load(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> int:
    from .config import load_config

    ap = argparse.ArgumentParser(description="LP против HODL по снимкам в actions.jsonl")
    ap.add_argument("--since", type=float, default=None,
                    help="unix ts начала периода (после пополнения капитала)")
    args = ap.parse_args()

    rep = compute_report(_load(load_config().log_file), args.since)
    print(render_report(rep, now_ts=time.time()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
