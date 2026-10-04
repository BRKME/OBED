"""
Сверка конфига инстанса с цепью — до того, как туда пойдут деньги.

Только чтение, ключ не нужен. Адреса контрактов сверяются между собой:
NPM.factory() и SwapRouter02.factory() должны указывать на factory из
конфига, WETH9 у обоих — совпадать с wrapped_native, пул — быть тем, что
factory.getPool() возвращает для этой пары и fee tier. Опечатка или чужой
адрес в любом месте даёт красный ран, а не потерянные деньги.

    OBED_CONFIG=config.bsc2.yaml python -m src.verify

Код возврата 1 — расхождение (или инстанс включён, но не дозаполнен).
"""
import sys

from web3 import Web3


def _eq(a, b) -> bool:
    return bool(a) and bool(b) and str(a).lower() == str(b).lower()


def _opt(cfg, name: str) -> str:
    """Поле конфига или "" — свойства pool_address/withdrawal_address бросают на пустом."""
    try:
        return getattr(cfg, name) or ""
    except ValueError:
        return ""


def todo(cfg) -> list:
    """Что ещё не заполнено. Для выключенного инстанса — не ошибка."""
    pool_addr, wd_addr = _opt(cfg, "pool_address"), _opt(cfg, "withdrawal_address")
    items = []
    if not cfg.wrapped_native:
        items.append("contracts.wrapped_native — вписать значение NPM.WETH9() из вывода ниже")
    if not pool_addr:
        items.append("pool.address / token0 / token1 / fee_tier — пара не выбрана")
    if not cfg.payout_token_address:
        items.append("fees.payout_token_address")
    if not wd_addr:
        items.append("fees.withdrawal_address — адрес «на обед» в этой сети")
    return items


def check(facts: dict, cfg) -> list:
    """Расхождения конфига с цепью. Пустой список — всё сходится."""
    pool_addr, wd_addr = _opt(cfg, "pool_address"), _opt(cfg, "withdrawal_address")
    p = []
    if facts["chain_id"] != cfg.chain_id:
        p.append(f"chain_id сети {facts['chain_id']}, в конфиге {cfg.chain_id}")
    if not _eq(facts["npm_factory"], cfg.factory):
        p.append(f"NPM.factory() = {facts['npm_factory']}, в конфиге factory {cfg.factory}")
    if not _eq(facts["router_factory"], cfg.factory):
        p.append(f"SwapRouter02.factory() = {facts['router_factory']}, "
                 f"в конфиге factory {cfg.factory}")
    if not _eq(facts["npm_weth9"], facts["router_weth9"]):
        p.append(f"WETH9 у NPM {facts['npm_weth9']} и у роутера {facts['router_weth9']} разные")
    if cfg.wrapped_native and not _eq(facts["npm_weth9"], cfg.wrapped_native):
        p.append(f"NPM.WETH9() = {facts['npm_weth9']}, в конфиге wrapped_native "
                 f"{cfg.wrapped_native}")

    if pool_addr:
        if not _eq(facts["pool_from_factory"], pool_addr):
            p.append(f"factory.getPool(token0, token1, {cfg.fee_tier}) = "
                     f"{facts['pool_from_factory']}, в конфиге пул {pool_addr}")
        if not (_eq(facts["pool_token0"], cfg.pool_token0)
                and _eq(facts["pool_token1"], cfg.pool_token1)):
            p.append(f"пул отдаёт token0={facts['pool_token0']} token1={facts['pool_token1']}, "
                     f"в конфиге {cfg.pool_token0} / {cfg.pool_token1} (порядок важен)")
        if facts["pool_fee"] != cfg.fee_tier:
            p.append(f"fee пула {facts['pool_fee']}, в конфиге {cfg.fee_tier}")
        if cfg.payout_token_address and not (
                _eq(cfg.payout_token_address, cfg.pool_token0)
                or _eq(cfg.payout_token_address, cfg.pool_token1)):
            p.append("payout-токен не входит в пул — своп комиссий не пройдёт")

    if wd_addr and facts.get("withdrawal_code_size"):
        p.append(f"withdrawal_address {wd_addr} — контракт, а не кошелёк: "
                 f"может не принять перевод")
    return p


def gather(cfg) -> dict:
    pool_addr, wd_addr = _opt(cfg, "pool_address"), _opt(cfg, "withdrawal_address")
    from .abis import FACTORY_ABI, POOL_ABI, POSITION_MANAGER_ABI, SWAP_ROUTER02_ABI
    from .web3_client import connect

    w3 = connect(cfg.rpc_urls)
    c = lambda addr, abi: w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)  # noqa: E731
    npm = c(cfg.position_manager, POSITION_MANAGER_ABI)
    router = c(cfg.swap_router02, SWAP_ROUTER02_ABI)
    facts = {
        "chain_id": w3.eth.chain_id,
        "npm_factory": npm.functions.factory().call(),
        "npm_weth9": npm.functions.WETH9().call(),
        "router_factory": router.functions.factory().call(),
        "router_weth9": router.functions.WETH9().call(),
        "pool_from_factory": None, "pool_token0": None, "pool_token1": None, "pool_fee": None,
        "withdrawal_code_size": 0,
    }
    if pool_addr:
        factory = c(cfg.factory, FACTORY_ABI)
        facts["pool_from_factory"] = factory.functions.getPool(
            Web3.to_checksum_address(cfg.pool_token0), Web3.to_checksum_address(cfg.pool_token1),
            cfg.fee_tier).call()
        pool = c(pool_addr, POOL_ABI)
        facts["pool_token0"] = pool.functions.token0().call()
        facts["pool_token1"] = pool.functions.token1().call()
        facts["pool_fee"] = pool.functions.fee().call()
    if wd_addr:
        facts["withdrawal_code_size"] = len(
            w3.eth.get_code(Web3.to_checksum_address(wd_addr)))
    return facts


def main() -> int:
    from .config import load_config

    cfg = load_config()
    print(f"## verify: {cfg.name} (chain {cfg.chain_id})\n")
    facts = gather(cfg)
    for k, v in facts.items():
        print(f"- {k}: `{v}`")

    problems = check(facts, cfg)
    missing = todo(cfg)
    if problems:
        print("\n**Расхождения:**")
        for x in problems:
            print(f"- ❌ {x}")
    if missing:
        print("\n**Не заполнено:**")
        for x in missing:
            print(f"- ⏳ {x}")
    if not problems and not missing:
        print("\n✅ Всё сходится с цепью.")
    elif not problems:
        print("\n✅ Контракты сходятся с цепью; осталось дозаполнить конфиг.")

    _print_wallet(cfg)

    if problems or (cfg.enabled and missing):
        return 1
    return 0


def _print_wallet(cfg) -> None:
    """
    Адрес кошелька бота и его балансы — чтобы оператор видел, куда пополнять,
    и что секрет с ключом добавлен. Печатается только адрес (он публичный),
    сам ключ — никогда. Без ключа — просто сообщение, не ошибка.
    """
    from eth_account import Account
    from .abis import ERC20_ABI
    from .web3_client import connect

    try:
        address = Account.from_key(cfg.private_key).address
    except Exception:  # noqa: BLE001 — нет секрета или он кривой; текст ошибки не печатаем
        print(f"\n**Кошелёк бота:** секрет `{cfg.private_key_env}` не задан или не читается.")
        return

    w3 = connect(cfg.rpc_urls)
    native = w3.eth.get_balance(address) / 1e18
    print(f"\n**Кошелёк бота:** `{address}`")
    print(f"- нативная монета (газ): {native:.6f}")
    tokens = {"wrapped_native": cfg.wrapped_native,
              "token0": cfg.pool_token0, "token1": cfg.pool_token1}
    seen = set()
    for label, addr in tokens.items():
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        t = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
        bal = t.functions.balanceOf(address).call() / 10 ** t.functions.decimals().call()
        print(f"- {label} `{addr}`: {bal:.6f}")
    if native < cfg.min_gas_native:
        print(f"- ⚠️ газа меньше {cfg.min_gas_native} — бот не начнёт работу")


if __name__ == "__main__":
    sys.exit(main())
