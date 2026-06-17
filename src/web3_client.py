from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account

from .abis import ERC20_ABI, POOL_ABI, FACTORY_ABI, POSITION_MANAGER_ABI, SWAP_ROUTER02_ABI
from .logger import logger


def connect(rpc_urls: list) -> Web3:
    last_err = None
    for url in rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            if w3.is_connected():
                logger.info("Подключен к RPC: %s", url)
                return w3
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("RPC недоступен (%s): %s", url, e)
    raise ConnectionError(f"Не удалось подключиться ни к одному RPC. Последняя ошибка: {last_err}")


class ChainClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.w3 = connect(cfg.rpc_urls)
        self.account = Account.from_key(cfg.private_key)

        self.pool = self.w3.eth.contract(address=Web3.to_checksum_address(cfg.pool_address), abi=POOL_ABI)
        self.factory = self.w3.eth.contract(address=Web3.to_checksum_address(cfg.factory), abi=FACTORY_ABI)
        self.position_manager = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg.position_manager), abi=POSITION_MANAGER_ABI)
        self.swap_router = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg.swap_router02), abi=SWAP_ROUTER02_ABI)

    def erc20(self, address: str):
        return self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)

    def send_tx(self, func_call, value: int = 0) -> dict:
        """Подписывает и отправляет транзакцию, ждёт receipt. Возвращает receipt (dict-like)."""
        addr = self.account.address
        nonce = self.w3.eth.get_transaction_count(addr, "pending")
        try:
            gas_estimate = func_call.estimate_gas({"from": addr, "value": value})
        except Exception as e:  # noqa: BLE001
            logger.error("Не удалось оценить газ: %s", e)
            raise
        gas_limit = int(gas_estimate * 1.3)

        tx = func_call.build_transaction({
            "from": addr,
            "nonce": nonce,
            "value": value,
            "gas": gas_limit,
            "chainId": self.cfg.chain_id,
        })
        # EIP-1559 если сеть поддерживает, иначе legacy gasPrice уже выставлен build_transaction
        if "maxFeePerGas" not in tx:
            tx["gasPrice"] = self.w3.eth.gas_price

        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        logger.info("Транзакция отправлена: %s", tx_hash.hex())
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status != 1:
            raise RuntimeError(f"Транзакция {tx_hash.hex()} завершилась с ошибкой (status=0)")
        return receipt

    def ensure_allowance(self, token_address: str, spender: str, amount: int) -> None:
        token = self.erc20(token_address)
        current = token.functions.allowance(self.account.address, Web3.to_checksum_address(spender)).call()
        if current >= amount:
            return
        logger.info("Approve %s для spender %s", token_address, spender)
        # некоторые токены требуют сначала сбросить allowance в 0
        if current > 0:
            self.send_tx(token.functions.approve(Web3.to_checksum_address(spender), 0))
        self.send_tx(token.functions.approve(Web3.to_checksum_address(spender), amount))
