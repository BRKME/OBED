import math
import unittest

from src import math_utils

# sqrtPriceX96 для price_raw = 0.75 (token1 за token0, raw units)
SQRT_075_X96 = int(math.sqrt(0.75) * 2 ** 96)


class TestMinAmountOut(unittest.TestCase):
    def test_zero_for_one_basic(self):
        """Своп token0 -> token1: out = in * price, минус fee пула, минус slippage."""
        amount_in = 10 ** 18  # 1 токен raw
        # fee 1% (10000 ppm), slippage 1% (100 bps)
        result = math_utils.min_amount_out(
            SQRT_075_X96, amount_in, fee_ppm=10000, slippage_bps=100, zero_for_one=True)
        expected = int(amount_in * 0.75 * 0.99 * 0.99)
        self.assertAlmostEqual(result, expected, delta=expected * 1e-9)

    def test_one_for_zero_basic(self):
        """Своп token1 -> token0: out = in / price."""
        amount_in = 10 ** 18
        result = math_utils.min_amount_out(
            SQRT_075_X96, amount_in, fee_ppm=10000, slippage_bps=100, zero_for_one=False)
        expected = int(amount_in / 0.75 * 0.99 * 0.99)
        self.assertAlmostEqual(result, expected, delta=expected * 1e-9)

    def test_zero_slippage_zero_fee(self):
        """Без fee и slippage — просто пересчёт по цене."""
        amount_in = 2 * 10 ** 18
        result = math_utils.min_amount_out(
            SQRT_075_X96, amount_in, fee_ppm=0, slippage_bps=0, zero_for_one=True)
        expected = amount_in * 0.75
        self.assertAlmostEqual(result, expected, delta=expected * 1e-12)

    def test_result_is_int_and_positive(self):
        result = math_utils.min_amount_out(
            SQRT_075_X96, 12345678, fee_ppm=10000, slippage_bps=100, zero_for_one=True)
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_min_out_never_exceeds_fair_value(self):
        """min_out всегда строго меньше идеального обмена без комиссий."""
        amount_in = 10 ** 18
        fair = amount_in * 0.75
        result = math_utils.min_amount_out(
            SQRT_075_X96, amount_in, fee_ppm=10000, slippage_bps=100, zero_for_one=True)
        self.assertLess(result, fair)


if __name__ == "__main__":
    unittest.main()
