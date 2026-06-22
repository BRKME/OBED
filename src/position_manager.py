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

    # баланс мог измениться после свопа — перечитываем
    bal0, bal1 = _wallet_balances(client, pool_state)

    tick_lower, tick_upper = math_utils.symmetric_range_ticks(
        pool_state["tick"], pool_state["sqrt_price_x96"], cfg.range_width_pct / 100,
        pool_state["tick_spacing"])

    client.ensure_allowance(pool_state["token0"], cfg.position_manager, bal0)
    client.ensure_allowance(pool_state["token1"], cfg.position_manager, bal1)

    slip = cfg.slippage_bps / 10000
    params = (
        Web3.to_checksum_address(pool_state["token0"]),
        Web3.to_checksum_address(pool_state["token1"]),
        pool_state["fee"],
        tick_lower,
        tick_upper,
        bal0,
        bal1,
        int(bal0 * (1 - slip)),
        int(bal1 * (1 - slip)),
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
    "Тычет" позицию decreaseLiquidity(0), чтобы обновить tokensOwed, затем проверяет
    стоимость накопленных комиссий в пересчёте на payout-токен. Если порог достигнут —
    забирает только комиссии (ликвидность не трогая), конвертирует второй токен в
    payout-токен через тот же пул и шлёт всё на withdrawal_address.
    """
    poke_params = (token_id, 0, 0, 0, int(time.time()) + DEADLINE_SECONDS)
    client.send_tx(client.position_manager.functions.decreaseLiquidity(poke_params))

    pos = client.position_manager.functions.positions(token_id).call()
    tokens_owed0, tokens_owed1 = pos[10], pos[11]

    is_payout0 = payout_is_token0(cfg, pool_state)
    fees_value = math_utils.fees_value_in_payout(
        tokens_owed0, tokens_owed1, pool_state["decimals0"], pool_state["decimals1"],
        pool_state["price_t1_per_t0"], is_payout0)

    logger.info("Накопленные комиссии: ~%.6f payout-токена (порог %.6f)",
                fees_value, cfg.fee_threshold_payout)

    if fees_value < cfg.fee_threshold_payout:
        return

    collect_params = (token_id, client.account.address, MAX_UINT128, MAX_UINT128)
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
        transfer_receipt = client.send_tx(
            client.erc20(payout_token).functions.transfer(
                Web3.to_checksum_address(cfg.withdrawal_address), payout_balance))
        logger.info("Комиссии отправлены на %s: %s (tx=%s)", cfg.withdrawal_address,
                    payout_balance, transfer_receipt.transactionHash.hex())
        log_action(cfg.log_file, "withdraw_fees", price=pool_state["price_t1_per_t0"],
                   tx_hash=transfer_receipt.transactionHash.hex(), fees_usd=fees_value,
                   token_id=token_id, amount_payout=payout_balance)
