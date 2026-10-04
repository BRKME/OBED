import time
from web3 import Web3

from . import math_utils
from .swap import swap_exact_in
from .logger import logger, log_action

DEADLINE_SECONDS = 600
MAX_UINT128 = 2 ** 128 - 1


def get_pool_state(client) -> dict:
    slot0 = client.pool.functions.slot0().call()
    sqrt_price_x96, tick = slot0[0], slot0[1]
    tick_spacing = client.pool.functions.tickSpacing().call()
    token0 = client.pool.functions.token0().call()
    token1 = client.pool.functions.token1().call()
    fee = client.pool.functions.fee().call()

    dec0 = client.erc20(token0).functions.decimals().call()
    dec1 = client.erc20(token1).functions.decimals().call()

    price = math_utils.human_price(sqrt_price_x96, dec0, dec1)

    return {
        "sqrt_price_x96": sqrt_price_x96,
        "tick": tick,
        "tick_spacing": tick_spacing,
        "token0": token0,
        "token1": token1,
        "decimals0": dec0,
        "decimals1": dec1,
        "fee": fee,
        "price_t1_per_t0": price,
    }


def is_in_range(pool_state: dict, tick_lower: int, tick_upper: int) -> bool:
    return tick_lower <= pool_state["tick"] < tick_upper


def payout_is_token0(cfg, pool_state: dict) -> bool:
    return Web3.to_checksum_address(pool_state["token0"]) == Web3.to_checksum_address(cfg.payout_token_address)


def _wallet_balances(client, pool_state: dict) -> tuple:
    bal0 = client.erc20(pool_state["token0"]).functions.balanceOf(client.account.address).call()
    bal1 = client.erc20(pool_state["token1"]).functions.balanceOf(client.account.address).call()
    return bal0, bal1


def rebalance_to_50_50(client, pool_state: dict, slippage_bps: int) -> None:
    """
    Доводит баланс кошелька по token0/token1 до примерно равной стоимости,
    меняя избыток одного токена на другой через тот же пул.
    """
    bal0, bal1 = _wallet_balances(client, pool_state)
    price = pool_state["price_t1_per_t0"]  # token1 за token0, human units
    dec0, dec1 = pool_state["decimals0"], pool_state["decimals1"]

    val0_in_t1 = (bal0 / (10 ** dec0)) * price  # стоимость token0-баланса в единицах token1
    val1_in_t1 = bal1 / (10 ** dec1)

    total_in_t1 = val0_in_t1 + val1_in_t1
    if total_in_t1 <= 0:
        raise ValueError("Нулевой баланс token0/token1 на кошельке — нечем открывать позицию")

    target_each_in_t1 = total_in_t1 / 2
    diff_in_t1 = val0_in_t1 - target_each_in_t1  # >0 значит избыток в token0

    # не размениваем совсем мелкую разницу (меньше 0.5% портфеля) — экономим газ
    if abs(diff_in_t1) / total_in_t1 < 0.005:
        logger.info("Баланс уже близок к 50/50, своп для ребаланса не требуется")
        return

    if diff_in_t1 > 0:
        # избыток token0 -> меняем часть token0 на token1
        amount0_to_swap_human = diff_in_t1 / price
        amount_in = int(amount0_to_swap_human * (10 ** dec0))
        swap_exact_in(client, pool_state["token0"], pool_state["token1"], pool_state["fee"],
                      amount_in, slippage_bps)
    else:
        amount1_to_swap_human = -diff_in_t1
        amount_in = int(amount1_to_swap_human * (10 ** dec1))
        swap_exact_in(client, pool_state["token1"], pool_state["token0"], pool_state["fee"],
                      amount_in, slippage_bps)


def hydrate_position(client, token_id: int) -> dict:
    """
    Читает существующую позицию из контракта positions(token_id) и возвращает
    её границы. Используется для подхвата позиции, открытой вручную, когда в
    state.json указан только token_id без тиков.
    Проверяет, что позиция жива (liquidity > 0) и принадлежит токенам нашего пула.
    """
    pos = client.position_manager.functions.positions(token_id).call()
    token0, token1 = pos[2], pos[3]
    fee = pos[4]
    tick_lower, tick_upper = pos[5], pos[6]
    liquidity = pos[7]

    expected0 = Web3.to_checksum_address(client.cfg.pool_token0)
    expected1 = Web3.to_checksum_address(client.cfg.pool_token1)
    if (Web3.to_checksum_address(token0) != expected0 or
            Web3.to_checksum_address(token1) != expected1):
        raise ValueError(
            f"Позиция {token_id} относится к другой паре токенов "
            f"({token0}/{token1}), а не к пулу из конфига")

    if liquidity == 0:
        raise ValueError(f"Позиция {token_id} имеет нулевую ликвидность (закрыта?)")

    logger.info("Подхвачена существующая позиция %s: tickLower=%s tickUpper=%s liquidity=%s",
                token_id, tick_lower, tick_upper, liquidity)
    return {"token_id": token_id, "tick_lower": tick_lower, "tick_upper": tick_upper}


def open_position(client, cfg, pool_state: dict) -> dict:
    rebalance_to_50_50(client, pool_state, cfg.slippage_bps)

    # Ребаланс-своп мог сдвинуть цену (особенно в маленьком пуле) — перечитываем
    # состояние пула заново, чтобы новый диапазон был центрирован на актуальной цене,
    # а не на досвоповой. Иначе пропорция токенов не совпадёт с диапазоном и mint
    # упадёт на Price slippage check.
    pool_state = get_pool_state(client)
    bal0, bal1 = _wallet_balances(client, pool_state)

    tick_lower, tick_upper = math_utils.symmetric_range_ticks(
        pool_state["tick"], pool_state["sqrt_price_x96"], cfg.range_width_pct / 100,
        pool_state["tick_spacing"])

    client.ensure_allowance(pool_state["token0"], cfg.position_manager, bal0)
    client.ensure_allowance(pool_state["token1"], cfg.position_manager, bal1)

    # amount*Min = 0: при двустороннем mint контракт берёт 100% лимитирующего токена
    # и часть второго по пропорции диапазона, поэтому требовать высокий минимум по обоим
    # нельзя — остаток просто останется на кошельке до следующего цикла. Защита от
    # манипуляции ценой здесь — сам факт фиксированного ценового диапазона и deadline.
    params = (
        Web3.to_checksum_address(pool_state["token0"]),
        Web3.to_checksum_address(pool_state["token1"]),
        pool_state["fee"],
        tick_lower,
        tick_upper,
        bal0,
        bal1,
        0,
        0,
        client.account.address,
        int(time.time()) + DEADLINE_SECONDS,
    )
    func = client.position_manager.functions.mint(params)
    receipt = client.send_tx(func)

    # tokenId достаём из события IncreaseLiquidity/Transfer — проще запросить
    # последний tokenId владельца через balanceOf/tokenOfOwnerByIndex, но у нас минимальный
    # ABI без ERC721Enumerable. Разбираем по логам Transfer(0x0 -> recipient).
    token_id = _extract_minted_token_id(client, receipt)

    logger.info("Позиция открыта: tokenId=%s tickLower=%s tickUpper=%s tx=%s",
                token_id, tick_lower, tick_upper, receipt.transactionHash.hex())
    log_action(cfg.log_file, "open_position", price=pool_state["price_t1_per_t0"],
               tx_hash=receipt.transactionHash.hex(), token_id=token_id,
               tick_lower=tick_lower, tick_upper=tick_upper)

    return {"token_id": token_id, "tick_lower": tick_lower, "tick_upper": tick_upper}


def _extract_minted_token_id(client, receipt) -> int:
    transfer_topic = client.w3.keccak(text="Transfer(address,address,uint256)").hex()
    zero_addr_topic = "0x" + "0" * 64
    for log in receipt.logs:
        if log.address.lower() != client.cfg.position_manager.lower():
            continue
        if len(log.topics) == 4 and log.topics[0].hex() == transfer_topic and log.topics[1].hex() == zero_addr_topic:
            return int(log.topics[3].hex(), 16)
    raise RuntimeError("Не удалось найти tokenId новой позиции в логах транзакции mint()")


def close_position(client, cfg, token_id: int, pool_state: dict) -> None:
    pos = client.position_manager.functions.positions(token_id).call()
    liquidity = pos[7]

    if liquidity > 0:
        dec_params = (token_id, liquidity, 0, 0, int(time.time()) + DEADLINE_SECONDS)
        receipt = client.send_tx(client.position_manager.functions.decreaseLiquidity(dec_params))
        logger.info("Ликвидность снята: tokenId=%s tx=%s", token_id, receipt.transactionHash.hex())

    collect_params = (token_id, client.account.address, MAX_UINT128, MAX_UINT128)
    receipt = client.send_tx(client.position_manager.functions.collect(collect_params))
    logger.info("Средства собраны с позиции: tokenId=%s tx=%s", token_id, receipt.transactionHash.hex())

    receipt = client.send_tx(client.position_manager.functions.burn(token_id))
    logger.info("Позиция закрыта (burn): tokenId=%s tx=%s", token_id, receipt.transactionHash.hex())

    log_action(cfg.log_file, "close_position", price=pool_state["price_t1_per_t0"],
               tx_hash=receipt.transactionHash.hex(), token_id=token_id)


def check_and_collect_fees(client, cfg, token_id: int, pool_state: dict) -> None:
    """
    Оценивает накопленные комиссии статическим вызовом collect.call() (без транзакции
    и газа) — это самый надёжный способ узнать сумму, не трогая ликвидность и не
    завися от того, как форк реализует decreaseLiquidity(0). Если стоимость комиссий
    в пересчёте на payout-токен достигла порога — собирает их реальной транзакцией,
    конвертирует второй токен в payout-токен через тот же пул и шлёт на withdrawal_address.
    """
    collect_params = (token_id, client.account.address, MAX_UINT128, MAX_UINT128)

    # статическая симуляция: сколько токенов вернёт collect прямо сейчас
    owed0, owed1 = client.position_manager.functions.collect(collect_params).call(
        {"from": client.account.address})

    is_payout0 = payout_is_token0(cfg, pool_state)
    fees_value = math_utils.fees_value_in_payout(
        owed0, owed1, pool_state["decimals0"], pool_state["decimals1"],
        pool_state["price_t1_per_t0"], is_payout0)

    logger.info("Накопленные комиссии: ~%.6f payout-токена (порог %.6f)",
                fees_value, cfg.fee_threshold_payout)

    if fees_value < cfg.fee_threshold_payout:
        return

    receipt = client.send_tx(client.position_manager.functions.collect(collect_params))
    logger.info("Комиссии собраны: tokenId=%s tx=%s", token_id, receipt.transactionHash.hex())

    # конвертируем второй токен в payout-токен через тот же пул
    payout_token = pool_state["token0"] if is_payout0 else pool_state["token1"]
    other_token = pool_state["token1"] if is_payout0 else pool_state["token0"]
    other_balance = client.erc20(other_token).functions.balanceOf(client.account.address).call()

    if other_balance > 0:
        swap_exact_in(client, other_token, payout_token, pool_state["fee"], other_balance,
                      cfg.slippage_bps)

    payout_balance = client.erc20(payout_token).functions.balanceOf(client.account.address).call()
    if payout_balance > 0:
        transfer_receipt, asset = _send_payout(client, cfg, payout_token, payout_balance)
        payout_dec = pool_state["decimals0"] if is_payout0 else pool_state["decimals1"]
        payout_human = payout_balance / 10 ** payout_dec
        payout_value_t1 = payout_human * pool_state["price_t1_per_t0"] if is_payout0 else payout_human
        log_action(cfg.log_file, "withdraw_fees", price=pool_state["price_t1_per_t0"],
                   tx_hash=transfer_receipt.transactionHash.hex(), fees_usd=fees_value,
                   token_id=token_id, amount_payout=payout_balance, asset=asset,
                   payout_value_t1=payout_value_t1)


def _send_payout(client, cfg, payout_token: str, amount: int) -> tuple:
    """
    Отправляет комиссии на withdrawal_address. Возвращает (receipt, asset).

    Обёртку нативной монеты (WBNB/WETH) разворачиваем и шлём нативной монетой.
    22.08.2026: раньше уходил ERC-20 WBNB — на свежем кошельке без газа получатель
    не мог его ни развернуть, ни перевести, а на биржевой депозит WBNB не зачисляется.
    Любой другой payout-токен (USDC и т.п.) развернуть нельзя — шлём как ERC-20.
    """
    if Web3.to_checksum_address(payout_token) != Web3.to_checksum_address(cfg.wrapped_native):
        receipt = client.send_tx(client.erc20(payout_token).functions.transfer(
            Web3.to_checksum_address(cfg.withdrawal_address), amount))
        logger.info("Комиссии отправлены (ERC-20 %s) на %s: %s",
                    payout_token, cfg.withdrawal_address, amount)
        return receipt, "erc20"

    native_before = client.w3.eth.get_balance(client.account.address)
    client.send_tx(client.wnative(payout_token).functions.withdraw(amount))
    native_after = client.w3.eth.get_balance(client.account.address)
    # отправляем ровно развёрнутую сумму, газовый резерв бота не трогаем
    unwrapped = max(0, native_after - native_before)
    amount_to_send = min(amount, unwrapped) if unwrapped else amount
    receipt = client.send_native(cfg.withdrawal_address, amount_to_send)
    logger.info("Комиссии отправлены (нативная монета) на %s: %s",
                cfg.withdrawal_address, amount_to_send)
    return receipt, "native"


def native_value_t1(native: float, wrapped_native: str, token0: str, token1: str,
                    price_t1_per_t0: float):
    """Нативная монета в единицах token1; None, если её нет в паре (цену не знаем)."""
    if not wrapped_native:
        return None
    w = Web3.to_checksum_address(wrapped_native)
    if w == Web3.to_checksum_address(token1):
        return native
    if w == Web3.to_checksum_address(token0):
        return native * price_t1_per_t0
    return None


def take_snapshot(client, cfg, pool_state: dict, position) -> None:
    """
    Пишет в журнал снимок всего, что есть у бота: свободные токены на кошельке,
    токены внутри позиции, невыведенные комиссии, нативную монету. Только чтение
    (eth_call), газа не тратит. Из этих снимков src/lp_stats.py считает LP против HODL.

    Токены внутри позиции — статический вызов decreaseLiquidity на всю ликвидность:
    контракт сам считает, сколько вернул бы, без собственной математики тиков.
    """
    dec0, dec1 = pool_state["decimals0"], pool_state["decimals1"]
    sender = {"from": client.account.address}

    free0, free1 = _wallet_balances(client, pool_state)
    pos0 = pos1 = fee0 = fee1 = 0
    token_id = None
    if position is not None:
        token_id = position["token_id"]
        liquidity = client.position_manager.functions.positions(token_id).call()[7]
        if liquidity > 0:
            dec_params = (token_id, liquidity, 0, 0, int(time.time()) + DEADLINE_SECONDS)
            pos0, pos1 = client.position_manager.functions.decreaseLiquidity(
                dec_params).call(sender)
        collect_params = (token_id, client.account.address, MAX_UINT128, MAX_UINT128)
        fee0, fee1 = client.position_manager.functions.collect(collect_params).call(sender)

    native = client.w3.eth.get_balance(client.account.address)

    log_action(cfg.log_file, "snapshot", price=pool_state["price_t1_per_t0"],
               token_id=token_id,
               free0=free0 / 10 ** dec0, free1=free1 / 10 ** dec1,
               pos0=pos0 / 10 ** dec0, pos1=pos1 / 10 ** dec1,
               fee0=fee0 / 10 ** dec0, fee1=fee1 / 10 ** dec1,
               native=native / 1e18,
               native_t1=native_value_t1(native / 1e18, cfg.wrapped_native,
                                         pool_state["token0"], pool_state["token1"],
                                         pool_state["price_t1_per_t0"]))
    return free0, free1


def detect_capital_flow(client, cfg, pool_state: dict, state: dict) -> None:
    """
    Находит пополнения и выводы оператора без сканирования блокчейна.

    Между тиками бот ничего не делает, поэтому свободный баланс кошелька по
    token0/token1 может измениться только от чужих переводов. Сравниваем его
    в начале тика с тем, что записал снимок в конце прошлого тика
    (state["last_free"], сырые единицы). Разница — capital_flow.

    Базу сразу обнуляем: её заново выставит снимок в конце тика. Если тик
    упадёт без снимка, следующий тик не будет сверяться со старой базой, а
    запишет flow_check_skipped — разрыв, который виден в отчёте.

    Не видит: вывод ликвидности из позиции напрямую через NFT (в обход бота)
    и переводы нативной монеты (это газ, на капитал не влияет).
    """
    last = state.get("last_free")
    state["last_free"] = None
    if last is None:
        log_action(cfg.log_file, "flow_check_skipped", price=pool_state["price_t1_per_t0"])
        return

    free0, free1 = _wallet_balances(client, pool_state)
    raw0, raw1 = free0 - last[0], free1 - last[1]
    if raw0 == 0 and raw1 == 0:
        return

    d0 = raw0 / 10 ** pool_state["decimals0"]
    d1 = raw1 / 10 ** pool_state["decimals1"]
    logger.info("Обнаружен перевод капитала оператором: token0 %+.8f, token1 %+.8f", d0, d1)
    log_action(cfg.log_file, "capital_flow", price=pool_state["price_t1_per_t0"],
               d0=d0, d1=d1)
