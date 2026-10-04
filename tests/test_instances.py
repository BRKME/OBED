"""Второй инстанс (bsc2): конфиг, вывод комиссий, сверка контрактов."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from src import config as config_mod
from src import lp_stats
from src import position_manager as pm
from src import verify

ROOT = Path(__file__).resolve().parent.parent

WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x1111111111111111111111111111111111111111"


def _cfg(**over):
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    for k, v in over.items():
        raw[k] = v
    return config_mod.Config(raw=raw)


class TestConfig(unittest.TestCase):
    def test_env_selects_config_file(self):
        with mock.patch.dict(os.environ, {"OBED_CONFIG": "config.bsc2.yaml"}):
            cfg = config_mod.load_config()
        self.assertEqual(cfg.name, "bsc2")

    def test_default_config_is_bsc(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBED_CONFIG", None)
            cfg = config_mod.load_config()
        self.assertEqual(cfg.chain_id, 56)
        self.assertTrue(cfg.enabled)

    def test_instances_never_share_state(self):
        bsc = config_mod.load_config(str(ROOT / "config.yaml"))
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        self.assertNotEqual(bsc.state_file, b2.state_file)
        self.assertNotEqual(bsc.log_file, b2.log_file)
        self.assertNotEqual(bsc.private_key_env, b2.private_key_env)

    def test_bsc_keeps_live_paths_and_key(self):
        # живой бот не должен потерять свою позицию из-за рефакторинга
        bsc = config_mod.load_config(str(ROOT / "config.yaml"))
        self.assertEqual(bsc.state_file, ROOT / "state/state.json")
        self.assertEqual(bsc.private_key_env, "BOT_PRIVATE_KEY")

    def test_private_key_read_from_instance_env(self):
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        with mock.patch.dict(os.environ, {b2.private_key_env: "ab" * 32}):
            self.assertEqual(b2.private_key, "0x" + "ab" * 32)

    def test_wrapped_native_falls_back_to_legacy_wbnb_key(self):
        bsc = config_mod.load_config(str(ROOT / "config.yaml"))
        self.assertEqual(bsc.wrapped_native.lower(),
                         "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")

    def test_bsc2_uses_same_chain_contracts_as_bsc(self):
        bsc = config_mod.load_config(str(ROOT / "config.yaml"))
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        for attr in ("chain_id", "factory", "position_manager", "swap_router02",
                     "wrapped_native", "withdrawal_address"):
            self.assertEqual(str(getattr(bsc, attr)).lower(), str(getattr(b2, attr)).lower(), attr)

    def test_bsc2_ships_disabled(self):
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        self.assertFalse(b2.enabled)

    def test_min_gas_default(self):
        self.assertAlmostEqual(_cfg().min_gas_native, 0.0003)


class _Tx:
    def __init__(self, log, name):
        self.log, self.name = log, name


class PayoutClient:
    def __init__(self):
        self.sent = []
        self.account = SimpleNamespace(address="0xme")
        self.native = 10 ** 18
        self.w3 = SimpleNamespace(eth=SimpleNamespace(get_balance=lambda a: self.native))

        def withdraw(amount):
            def run():
                self.native += amount
            return SimpleNamespace(run=run, kind="unwrap", amount=amount)

        self.wnative = lambda addr: SimpleNamespace(functions=SimpleNamespace(withdraw=withdraw))
        self.erc20 = lambda addr: SimpleNamespace(functions=SimpleNamespace(
            transfer=lambda to, amount: SimpleNamespace(
                run=lambda: None, kind="erc20", token=addr, to=to, amount=amount)))

    def send_tx(self, call):
        call.run()
        self.sent.append(call)
        return SimpleNamespace(transactionHash=b"\x01")

    def send_native(self, to, amount):
        self.sent.append(SimpleNamespace(kind="native", to=to, amount=amount))
        return SimpleNamespace(transactionHash=b"\x02")


class TestPayout(unittest.TestCase):
    def test_wrapped_native_is_unwrapped_and_sent_native(self):
        c = PayoutClient()
        cfg = SimpleNamespace(wrapped_native=WETH, withdrawal_address="0xlunch")
        receipt, asset = pm._send_payout(c, cfg, WETH, 5)
        self.assertEqual(asset, "native")
        self.assertEqual([s.kind for s in c.sent], ["unwrap", "native"])
        self.assertEqual(c.sent[-1].amount, 5)

    def test_other_token_sent_as_erc20(self):
        # USDC нельзя «развернуть» — раньше бот звал withdraw() на любом payout-токене
        c = PayoutClient()
        cfg = SimpleNamespace(wrapped_native=WETH, withdrawal_address="0x2222222222222222222222222222222222222222")
        receipt, asset = pm._send_payout(c, cfg, USDC, 7)
        self.assertEqual(asset, "erc20")
        self.assertEqual([s.kind for s in c.sent], ["erc20"])
        self.assertEqual(c.sent[0].amount, 7)


class TestStatsUnits(unittest.TestCase):
    def test_payout_value_t1_preferred_over_raw_wei(self):
        # USDC 6 знаков: сырые 20_000 = 0.02 USDC, а не 2e-14
        recs = [
            {"ts": 0, "action": "snapshot", "price": 1.0, "free0": 0, "free1": 0,
             "pos0": 1, "pos1": 1, "fee0": 0, "fee1": 0, "native": 0.01},
            {"ts": 1, "action": "withdraw_fees", "amount_payout": 20_000,
             "payout_value_t1": 0.02},
            {"ts": 2, "action": "snapshot", "price": 1.0, "free0": 0, "free1": 0,
             "pos0": 1, "pos1": 1, "fee0": 0, "fee1": 0, "native": 0.01},
        ]
        self.assertAlmostEqual(lp_stats.compute_report(recs)["withdrawn"], 0.02)

    def test_gas_uses_native_t1_when_present(self):
        # газ в ETH, а token1 — USDC: 0.001 ETH по 3000 = 3 USDC
        base = {"action": "snapshot", "price": 1.0, "free0": 0, "free1": 0,
                "pos0": 1, "pos1": 1, "fee0": 0, "fee1": 0}
        recs = [dict(base, ts=0, native=0.010, native_t1=30.0),
                dict(base, ts=1, native=0.009, native_t1=27.0)]
        self.assertAlmostEqual(lp_stats.compute_report(recs)["gas"], 3.0)

    def test_gas_unknown_when_native_not_priced(self):
        base = {"action": "snapshot", "price": 1.0, "free0": 0, "free1": 0,
                "pos0": 1, "pos1": 1, "fee0": 0, "fee1": 0}
        recs = [dict(base, ts=0, native=0.010, native_t1=None),
                dict(base, ts=1, native=0.009, native_t1=None)]
        rep = lp_stats.compute_report(recs)
        self.assertIsNone(rep["gas"])
        self.assertIn("газ не учтён", lp_stats.render_report(rep))

    def test_unit_label_rendered(self):
        base = {"action": "snapshot", "price": 1.0, "free0": 0, "free1": 0,
                "pos0": 1, "pos1": 1, "fee0": 0, "fee1": 0, "native": 0.01}
        rep = lp_stats.compute_report([dict(base, ts=0), dict(base, ts=1)])
        self.assertIn("USDC", lp_stats.render_report(rep, unit="USDC"))


class TestNativeValue(unittest.TestCase):
    def test_native_is_token1(self):
        self.assertAlmostEqual(pm.native_value_t1(2.0, WETH, USDC, WETH, 1500.0), 2.0)

    def test_native_is_token0(self):
        self.assertAlmostEqual(pm.native_value_t1(2.0, WETH, WETH, USDC, 1500.0), 3000.0)

    def test_native_not_in_pool(self):
        self.assertIsNone(pm.native_value_t1(2.0, WETH, USDC, USDC.replace("1", "3"), 1.0))


FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
POOL = "0x5555555555555555555555555555555555555555"


def _facts(**over):
    f = {
        "chain_id": 4663,
        "npm_factory": FACTORY, "npm_weth9": WETH,
        "router_factory": FACTORY, "router_weth9": WETH,
        "pool_from_factory": POOL,
        "pool_token0": WETH, "pool_token1": USDC, "pool_fee": 500,
        "withdrawal_code_size": 0,
    }
    f.update(over)
    return f


def _vcfg(**over):
    c = {
        "chain_id": 4663, "factory": FACTORY, "wrapped_native": WETH,
        "pool_address": POOL, "pool_token0": WETH, "pool_token1": USDC, "fee_tier": 500,
        "payout_token_address": USDC,
        "withdrawal_address": "0x2222222222222222222222222222222222222222",
    }
    c.update(over)
    return SimpleNamespace(**c)


class TestVerify(unittest.TestCase):
    def test_all_consistent(self):
        self.assertEqual(verify.check(_facts(), _vcfg()), [])

    def test_wrong_chain(self):
        self.assertTrue(verify.check(_facts(chain_id=1), _vcfg()))

    def test_router_from_other_factory(self):
        self.assertTrue(verify.check(_facts(router_factory=POOL), _vcfg()))

    def test_weth_mismatch(self):
        self.assertTrue(verify.check(_facts(npm_weth9=USDC), _vcfg()))

    def test_pool_not_from_factory(self):
        self.assertTrue(verify.check(_facts(pool_from_factory=WETH), _vcfg()))

    def test_token_order_swapped(self):
        self.assertTrue(verify.check(_facts(pool_token0=USDC, pool_token1=WETH), _vcfg()))

    def test_payout_not_in_pool(self):
        self.assertTrue(verify.check(_facts(), _vcfg(payout_token_address=POOL)))

    def test_withdrawal_is_contract_is_a_problem(self):
        # контракт (мультисиг, биржевой депозит в другой сети) может не принять перевод
        self.assertTrue(verify.check(_facts(withdrawal_code_size=100), _vcfg()))

    def test_pool_checks_skipped_when_pool_not_chosen(self):
        facts = _facts(pool_from_factory=None, pool_token0=None, pool_token1=None,
                       pool_fee=None)
        self.assertEqual(verify.check(facts, _vcfg(pool_address="")), [])


class TestDisabledInstance(unittest.TestCase):
    def test_disabled_does_nothing_and_needs_no_key(self):
        raw = yaml.safe_load((ROOT / "config.bsc2.yaml").read_text())
        raw["enabled"] = False
        with tempfile.TemporaryDirectory() as d:
            raw["paths"] = {"state_file": f"{d}/s.json", "log_file": f"{d}/a.jsonl"}
            p = Path(d) / "c.yaml"
            p.write_text(yaml.safe_dump(raw))
            env = {"OBED_CONFIG": str(p)}
            with mock.patch.dict(os.environ, env):
                os.environ.pop(raw["secrets"]["private_key_env"], None)
                from src import main
                with mock.patch.object(main, "ChainClient",
                                       side_effect=AssertionError("сеть тронута")):
                    self.assertEqual(main.run(), 0)
            self.assertFalse((Path(d) / "a.jsonl").exists())


class TestVerifyOnRealConfig(unittest.TestCase):
    """Регрессия 04.10: на незаполненном пуле verify падал на cfg.pool_address."""

    def _facts_for(self, cfg):
        return _facts(chain_id=cfg.chain_id, npm_factory=cfg.factory, router_factory=cfg.factory,
                      npm_weth9=cfg.wrapped_native, router_weth9=cfg.wrapped_native,
                      pool_from_factory=None, pool_token0=None, pool_token1=None,
                      pool_fee=None)

    def test_todo_on_unfilled_bsc2_config(self):
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        items = verify.todo(b2)
        self.assertTrue(any("pool" in x for x in items))
        self.assertFalse(any("withdrawal" in x for x in items))   # адрес тот же, что у bsc

    def test_check_on_unfilled_bsc2_config(self):
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        self.assertEqual(verify.check(self._facts_for(b2), b2), [])


class TestPayoutIsNativeBNB(unittest.TestCase):
    """Оператору нужен BNB, а не WBNB: оба BSC-инстанса должны разворачивать."""

    def _send(self, cfg_file):
        cfg = config_mod.load_config(str(ROOT / cfg_file))
        c = PayoutClient()
        _, asset = pm._send_payout(c, cfg, cfg.payout_token_address, 10 ** 16)
        return asset, [s.kind for s in c.sent]

    def test_bsc_sends_native(self):
        self.assertEqual(self._send("config.yaml"), ("native", ["unwrap", "native"]))

    def test_bsc2_sends_native(self):
        self.assertEqual(self._send("config.bsc2.yaml"), ("native", ["unwrap", "native"]))

    def test_verify_states_payout_asset(self):
        b2 = config_mod.load_config(str(ROOT / "config.bsc2.yaml"))
        self.assertIn("нативн", verify.payout_asset_line(b2))


if __name__ == "__main__":
    unittest.main()
