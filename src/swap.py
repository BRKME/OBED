from web3 import Web3

from . import math_utils
from .logger import logger


def swap_exact_in(client, token_in: str, token_out: str, fee_tier: int, amount_in: int,
                   slippage_bps: int) -> tuple:
    """
    Меняет amount_in token_in на token_out через пул с тем же fee_tier (используем тот же
    пул, в котором стоит позиция — для пары токенов позиции этого достаточно).
    Возвращает receipt.
    """
    if amount_in <= 0:
        return 0, None

    client.ensure_allowance(token_in, client.cfg.swap_router02, amount_in)

    # Защита от сэндвича/MEV: считаем минимальный выход по СВЕЖЕМУ slot0
    # (цена могла сдвинуться с момента чтения pool_state), минус комиссия пула,
    # минус допуск slippage_bps из конфига. Если кто-то сдвинет цену сильнее
    # допуска — своп ревертнётся, вместо того чтобы исполниться по плохому курсу.
    sqrt_price_x96 = client.pool.functions.slot0().call()[0]
    zero_for_one = (Web3.to_checksum_address(token_in) ==
                    Web3.to_checksum_address(client.cfg.pool_token0))
    amount_out_min = math_utils.min_amount_out(
        sqrt_price_x96, amount_in, fee_tier, slippage_bps, zero_for_one)

    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee_tier,
        client.account.address,
        amount_in,
        amount_out_min,
        0,
    )
    func = client.swap_router.functions.exactInputSingle(params)
    receipt = client.send_tx(func)
    logger.info("Своп выполнен: %s -> %s, amount_in=%s, min_out=%s, tx=%s", token_in, token_out,
                amount_in, amount_out_min, receipt.transactionHash.hex())
    return receipt
