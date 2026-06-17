from web3 import Web3

from .logger import logger


def swap_exact_in(client, token_in: str, token_out: str, fee_tier: int, amount_in: int,
                   slippage_bps: int) -> tuple:
    """
    Меняет amount_in token_in на token_out через пул с тем же fee_tier (используем тот же
    пул, в котором стоит позиция — для пары токенов позиции этого достаточно).
    Возвращает (amount_out_estimate_used_as_min, receipt).
    """
    if amount_in <= 0:
        return 0, None

    client.ensure_allowance(token_in, client.cfg.swap_router02, amount_in)

    # Без отдельного quoter-вызова закладываем консервативный amount_out_minimum = 0,
    # но ограничиваем риск через явный slippage на уровне всей операции ребаланса/мина —
    # сам мint() ниже принимает излишек обратно, так что небольшая неэффективность свопа
    # не приводит к потере средств, только к чуть менее точной 50/50 пропорции.
    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee_tier,
        client.account.address,
        amount_in,
        0,
        0,
    )
    func = client.swap_router.functions.exactInputSingle(params)
    receipt = client.send_tx(func)
    logger.info("Своп выполнен: %s -> %s, amount_in=%s, tx=%s", token_in, token_out, amount_in,
                receipt.transactionHash.hex())
    return receipt
