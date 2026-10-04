import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src import position_manager as pm

ME = "0x000000000000000000000000000000000000dEaD"


class _Call:
    def __init__(self, value):
        self.value = value
        self.kwargs = None

    def call(self, *args, **kwargs):
        return self.value


class FakeClient:
    """Отвечает на ровно те вызовы, которые делает take_snapshot."""

    def __init__(self, liquidity):
        self.account = SimpleNamespace(address=ME)
        self.liquidity = liquidity
        balances = {"T0": 5 * 10 ** 17, "T1": 2 * 10 ** 18}
        self.erc20 = lambda addr: SimpleNamespace(functions=SimpleNamespace(
            balanceOf=lambda a: _Call(balances[addr])))
        fns = SimpleNamespace(
            positions=lambda tid: _Call([0, 0, 0, 0, 0, 0, 0, self.liquidity]),
            decreaseLiquidity=lambda p: _Call((3 * 10 ** 18, 4 * 10 ** 18)),
            collect=lambda p: _Call((10 ** 16, 2 * 10 ** 16)),
        )
        self.position_manager = SimpleNamespace(functions=fns)
        self.w3 = SimpleNamespace(eth=SimpleNamespace(get_balance=lambda a: 7 * 10 ** 15))


POOL = {"token0": "T0", "token1": "T1", "decimals0": 18, "decimals1": 18,
        "price_t1_per_t0": 1.5}


class TestTakeSnapshot(unittest.TestCase):
    def _run(self, client, position):
        with tempfile.TemporaryDirectory() as d:
            cfg = SimpleNamespace(log_file=Path(d) / "a.jsonl")
            pm.take_snapshot(client, cfg, POOL, position)
            return json.loads(cfg.log_file.read_text().strip())

    def test_records_all_holdings_in_human_units(self):
        r = self._run(FakeClient(liquidity=123), {"token_id": 42})
        self.assertEqual(r["action"], "snapshot")
        self.assertEqual(r["token_id"], 42)
        self.assertAlmostEqual(r["price"], 1.5)
        self.assertAlmostEqual(r["free0"], 0.5)
        self.assertAlmostEqual(r["free1"], 2.0)
        self.assertAlmostEqual(r["pos0"], 3.0)
        self.assertAlmostEqual(r["pos1"], 4.0)
        self.assertAlmostEqual(r["fee0"], 0.01)
        self.assertAlmostEqual(r["fee1"], 0.02)
        self.assertAlmostEqual(r["native"], 0.007)

    def test_no_position_records_wallet_only(self):
        r = self._run(FakeClient(liquidity=0), None)
        self.assertEqual((r["pos0"], r["pos1"], r["fee0"], r["fee1"]), (0, 0, 0, 0))
        self.assertAlmostEqual(r["free1"], 2.0)

    def test_zero_liquidity_skips_decrease(self):
        r = self._run(FakeClient(liquidity=0), {"token_id": 42})
        self.assertEqual((r["pos0"], r["pos1"]), (0, 0))
        self.assertAlmostEqual(r["fee1"], 0.02)


if __name__ == "__main__":
    unittest.main()
