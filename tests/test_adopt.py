"""Подхват позиции, открытой оператором вручную (или потерянной из state)."""
import unittest
from types import SimpleNamespace

from src import position_manager as pm

T0 = "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb"
T1 = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
OTHER = "0x55d398326f99059fF775485246999027B3197955"
ME = "0x91dad140AF2800B2D660e530B9F42500Eee474a0"


class _Call:
    def __init__(self, v):
        self.v = v

    def call(self, *a, **k):
        return self.v


def _client(positions):
    """positions: {token_id: (token0, token1, fee, liquidity)}"""
    ids = list(positions)
    fns = SimpleNamespace(
        balanceOf=lambda owner: _Call(len(ids)),
        tokenOfOwnerByIndex=lambda owner, i: _Call(ids[i]),
        positions=lambda tid: _Call([0, 0, positions[tid][0], positions[tid][1],
                                     positions[tid][2], -100, 100, positions[tid][3]]),
    )
    return SimpleNamespace(account=SimpleNamespace(address=ME),
                           position_manager=SimpleNamespace(functions=fns))


CFG = SimpleNamespace(pool_token0=T0, pool_token1=T1, fee_tier=10000)


class TestFindOwnedPosition(unittest.TestCase):
    def test_none_owned(self):
        self.assertIsNone(pm.find_owned_position(_client({}), CFG))

    def test_finds_live_position_in_our_pool(self):
        self.assertEqual(pm.find_owned_position(_client({7: (T0, T1, 10000, 5)}), CFG), 7)

    def test_ignores_closed_position(self):
        self.assertIsNone(pm.find_owned_position(_client({7: (T0, T1, 10000, 0)}), CFG))

    def test_ignores_other_pair_and_other_fee(self):
        c = _client({7: (T0, OTHER, 10000, 5), 8: (T0, T1, 2500, 5)})
        self.assertIsNone(pm.find_owned_position(c, CFG))

    def test_picks_ours_among_others(self):
        c = _client({3: (T0, OTHER, 10000, 5), 9: (T0, T1, 10000, 5), 4: (T0, T1, 10000, 0)})
        self.assertEqual(pm.find_owned_position(c, CFG), 9)

    def test_two_live_positions_in_our_pool_is_an_error(self):
        # какую вести — решает оператор, а не бот
        c = _client({5: (T0, T1, 10000, 5), 6: (T0, T1, 10000, 7)})
        with self.assertRaises(RuntimeError):
            pm.find_owned_position(c, CFG)


if __name__ == "__main__":
    unittest.main()
