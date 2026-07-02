import sys

from .config import load_config
from .state import load_state, save_state, seconds_since_last_check, mark_checked_now
from .web3_client import ChainClient
from . import position_manager as pm
from .logger import logger, log_action


def run() -> int:
    cfg = load_config()
    state = load_state(cfg.state_file)

    elapsed = seconds_since_last_check(state)
    min_interval = cfg.check_interval_hours * 3600
    if elapsed is not None and elapsed < min_interval:
        logger.info("С последней проверки прошло %.0f сек (< %.0f сек) — выходим без действий",
                    elapsed, min_interval)
        return 0

    client = ChainClient(cfg)

    # Preflight по газу ДО любых транзакций. Кейс 02.07: reopen-цепочка из 7
    # транзакций высушила BNB и упала на ПОСЛЕДНЕМ шаге (mint), не хватило
    # $0.002 — позиция уже была закрыта, средства повисли вне пула. Порог —
    # с запасом на полный цикл close->swap->mint (~8 tx). При нехватке — выйти
    # громко (красный ран), НЕ трогая позицию и не сжигая остаток на approve.
    MIN_GAS_WEI = int(0.0003 * 1e18)   # ~10x стоимость mint на BSC
    native = client.w3.eth.get_balance(client.account.address)
    if native < MIN_GAS_WEI:
        logger.error(
            "Мало газа: %.6f BNB на %s (нужно >= %.4f BNB). Пополни кошелёк — "
            "тик пропущен ДО транзакций, позиция не тронута.",
            native / 1e18, client.account.address, MIN_GAS_WEI / 1e18)
        log_action(cfg.log_file, "low_gas", error=f"balance={native/1e18:.6f} BNB")
        mark_checked_now(state)
        save_state(cfg.state_file, state)
        return 1

    pool_state = pm.get_pool_state(client)
    logger.info("Текущая цена пула: %.8f (token1/token0), tick=%s",
                pool_state["price_t1_per_t0"], pool_state["tick"])

    position = state.get("position")

    try:
        if position is None:
            logger.info("Активной позиции нет — открываем первую позицию")
            new_pos = pm.open_position(client, cfg, pool_state)
            state["position"] = new_pos
            save_state(cfg.state_file, state)

        else:
            token_id = position["token_id"]

            # Подхват позиции, открытой вручную: если тиков нет в state — дочитываем из контракта
            if position.get("tick_lower") is None or position.get("tick_upper") is None:
                position = pm.hydrate_position(client, token_id)
                state["position"] = position
                save_state(cfg.state_file, state)

            in_range = pm.is_in_range(pool_state, position["tick_lower"], position["tick_upper"])

            if not in_range:
                logger.info("Цена вышла из диапазона [%s, %s] — переоткрываем позицию",
                            position["tick_lower"], position["tick_upper"])
                pm.close_position(client, cfg, token_id, pool_state)
                state["position"] = None
                save_state(cfg.state_file, state)

                # цена/баланс могли измениться после закрытия — перечитываем перед открытием
                pool_state = pm.get_pool_state(client)
                new_pos = pm.open_position(client, cfg, pool_state)
                state["position"] = new_pos
                save_state(cfg.state_file, state)
            else:
                logger.info("Позиция в диапазоне [%s, %s], tick=%s — проверяем комиссии",
                            position["tick_lower"], position["tick_upper"], pool_state["tick"])
                pm.check_and_collect_fees(client, cfg, token_id, pool_state)

    except Exception as e:  # noqa: BLE001
        logger.exception("Ошибка во время выполнения тика")
        log_action(cfg.log_file, "error", price=pool_state.get("price_t1_per_t0"),
                   error=str(e))
        mark_checked_now(state)
        save_state(cfg.state_file, state)
        return 1

    mark_checked_now(state)
    save_state(cfg.state_file, state)
    log_action(cfg.log_file, "tick_complete", price=pool_state["price_t1_per_t0"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
