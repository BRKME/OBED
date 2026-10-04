"""
Статистика LP: обгоняет ли позиция простое удержание токенов (HODL).

Источник — записи `snapshot` в actions.jsonl, которые бот пишет в конце каждого
тика (см. position_manager.take_snapshot). Всё считается в token1 (на BSC — WBNB).

  LP   = всё, что сейчас есть у бота (позиция + невыведенные комиссии + свободный
         баланс токенов) + выведенные на обед комиссии − потраченный газ
  HODL = токены первого снимка + пополнения − выводы оператора (capital_flow),
         оценённые по текущей цене

Разница LP − HODL — это комиссии минус IL минус издержки переоткрытий (свопы
через пул, проскальзывание) минус газ. Ответ на вопрос «стоит ли масштабировать».

Пополнения и выводы оператора бот находит сам (position_manager.detect_capital_flow)
и пишет как capital_flow: они входят и в LP, и в HODL, поэтому результат не искажают.
`--since <unix ts>` — чтобы посчитать с произвольного момента.

    python -m src.lp_stats [--since TS]
"""
import argparse
import json
import time
from pathlib import Path
from typing import Optional

MIN_DAYS_TO_JUDGE = 30
STALE_HOURS = 12       # тик раз в 4 ч: 12 ч без снимка — уже не случайность
PAYOUT_DECIMALS = 18   # старые записи без payout_value_t1: payout — WBNB (token1)


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
    withdrawn = sum(_payout_t1(r) for r in in_window if r.get("action") == "withdraw_fees")
    reopens = sum(1 for r in in_window if r.get("action") == "close_position")
    flows = [r for r in in_window if r.get("action") == "capital_flow"]
    hodl0 = h0 + sum(r["d0"] for r in flows)
    hodl1 = h1 + sum(r["d1"] for r in flows)
    net_flow = sum(r["d0"] * r["price"] + r["d1"] for r in flows)
    flow_gaps = sum(1 for r in records
                    if r.get("action") == "flow_check_skipped" and r["ts"] >= t0)
    # ошибки снимков считаем и после последнего снимка — там они важнее всего
    snapshot_errors = sum(1 for r in records
                          if r.get("action") == "snapshot_error" and r["ts"] >= t0)

    # газ — только падения нативного баланса между снимками; рост — это пополнение
    # оператором, а не доход позиции
    # Газ в token1. native_t1 = None — нативной монеты нет в паре, цену не знаем:
    # газ не учитываем и говорим об этом в отчёте. Старые снимки без native_t1 —
    # BSC, где нативная монета и есть token1 (WBNB).
    native_t1 = [s.get("native_t1", s["native"]) for s in snaps]
    if any(v is None for v in native_t1):
        gas = None
    else:
        gas = sum(max(0.0, a - b) for a, b in zip(native_t1, native_t1[1:]))

    start_value = h0 * first["price"] + h1
    equity = n0 * last["price"] + n1
    lp_value = equity + withdrawn - (gas or 0.0)
    hodl_value = hodl0 * last["price"] + hodl1
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
        "flows": len(flows),
        "net_flow": net_flow,
        "flow_gaps": flow_gaps,
    }


def _payout_t1(r: dict) -> float:
    if r.get("payout_value_t1") is not None:
        return r["payout_value_t1"]
    return (r.get("amount_payout") or 0) / 10 ** PAYOUT_DECIMALS


def render_report(rep: Optional[dict], now_ts: Optional[float] = None,
                  unit: str = "WBNB") -> str:
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
        f"| | {unit} |",
        "|---|---|",
        f"| на старте | {rep['start_value']:.4f} |",
        f"| пополнения − выводы ({rep['flows']}) | {rep['net_flow']:+.4f} |",
        f"| у бота сейчас | {rep['equity']:.4f} |",
        f"| выведено на обед | +{rep['withdrawn']:.4f} |",
        f"| газ | " + (f"−{rep['gas']:.4f}" if rep["gas"] is not None else "не учтён") + " |",
        f"| **LP итого** | **{rep['lp_value']:.4f}** |",
        f"| **HODL** | **{rep['hodl_value']:.4f}** |",
        "",
        f"**{verdict}: {sign}{rep['diff']:.4f} {unit} ({sign}{rep['diff_pct']:.2f}%)**",
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
    if rep["gas"] is None:
        lines += ["", "_газ не учтён: нативной монеты нет в паре, её цену бот не знает._"]
    if rep.get("flow_gaps"):
        lines += ["", f"⚠️ {rep['flow_gaps']} разрыв(а) в сверке пополнений (тик упал без "
                      f"снимка) — перевод в такой промежуток не виден, результат может "
                      f"быть искажён."]
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

    cfg = load_config()
    rep = compute_report(_load(cfg.log_file), args.since)
    print(f"## {cfg.name}\n")
    print(render_report(rep, now_ts=time.time(), unit=cfg.unit_label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
