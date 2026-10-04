import unittest

from src import lp_stats

DAY = 86400


def snap(ts, price, free0=0.0, free1=0.0, pos0=0.0, pos1=0.0, fee0=0.0, fee1=0.0,
         native=0.01):
    return {"ts": ts, "action": "snapshot", "price": price,
            "free0": free0, "free1": free1, "pos0": pos0, "pos1": pos1,
            "fee0": fee0, "fee1": fee1, "native": native}


def withdraw(ts, wbnb):
    return {"ts": ts, "action": "withdraw_fees", "amount_payout": int(wbnb * 1e18)}


class TestComputeReport(unittest.TestCase):
    def test_no_snapshots(self):
        self.assertIsNone(lp_stats.compute_report([{"ts": 1, "action": "tick_complete"}]))

    def test_flat_price_no_actions_equals_hodl(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            snap(DAY, 1.0, pos0=1, pos1=1),
        ])
        self.assertAlmostEqual(rep["lp_value"], 2.0)
        self.assertAlmostEqual(rep["hodl_value"], 2.0)
        self.assertAlmostEqual(rep["diff"], 0.0)

    def test_withdrawn_fees_count_as_lp_income(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            withdraw(DAY / 2, 0.02),
            snap(DAY, 1.0, pos0=1, pos1=1),
        ])
        self.assertAlmostEqual(rep["withdrawn"], 0.02)
        self.assertAlmostEqual(rep["diff"], 0.02)

    def test_unclaimed_fees_and_free_balance_are_equity(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            snap(DAY, 1.0, pos0=1, pos1=1, fee0=0.01, fee1=0.01, free1=0.005),
        ])
        self.assertAlmostEqual(rep["diff"], 0.025)

    def test_gas_is_cost_topup_is_not_income(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1, native=0.010),
            snap(DAY, 1.0, pos0=1, pos1=1, native=0.009),   # газ 0.001
            snap(2 * DAY, 1.0, pos0=1, pos1=1, native=0.050),  # пополнение
        ])
        self.assertAlmostEqual(rep["gas"], 0.001)
        self.assertAlmostEqual(rep["diff"], -0.001)

    def test_impermanent_loss_shows_as_negative_diff(self):
        # старт 1 ZEC + 1 WBNB при цене 1; цена x2, LP переложился в 0.7 ZEC + 1.4 WBNB
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            snap(DAY, 2.0, pos0=0.7, pos1=1.4),
        ])
        self.assertAlmostEqual(rep["start_value"], 2.0)
        self.assertAlmostEqual(rep["lp_value"], 2.8)
        self.assertAlmostEqual(rep["hodl_value"], 3.0)
        self.assertAlmostEqual(rep["diff"], -0.2)
        self.assertAlmostEqual(rep["diff_pct"], -0.2 / 3.0 * 100)

    def test_since_moves_baseline_and_ignores_older_withdrawals(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=5, pos1=5),
            withdraw(DAY / 2, 0.5),
            snap(DAY, 1.0, pos0=1, pos1=1),
            snap(2 * DAY, 1.0, pos0=1, pos1=1),
        ], since_ts=DAY)
        self.assertAlmostEqual(rep["start_value"], 2.0)
        self.assertAlmostEqual(rep["withdrawn"], 0.0)
        self.assertAlmostEqual(rep["days"], 1.0)

    def test_counts_reopens_in_window(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            {"ts": DAY / 2, "action": "close_position"},
            {"ts": DAY / 2, "action": "open_position"},
            snap(DAY, 1.0, pos0=1, pos1=1),
        ])
        self.assertEqual(rep["reopens"], 1)


class TestRender(unittest.TestCase):
    def _rep(self, days):
        return lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            snap(days * DAY, 1.0, pos0=1, pos1=1),
        ])

    def test_too_early_to_judge_under_30_days(self):
        self.assertIn("рано судить", lp_stats.render_report(self._rep(10)))

    def test_no_early_warning_after_30_days(self):
        self.assertNotIn("рано судить", lp_stats.render_report(self._rep(31)))

    def test_no_data_message(self):
        self.assertIn("нет", lp_stats.render_report(None).lower())


class TestStaleWarning(unittest.TestCase):
    def test_warns_when_last_snapshot_is_old(self):
        rep = lp_stats.compute_report([snap(0, 1.0, pos0=1, pos1=1)])
        self.assertIn("не обновлялась", lp_stats.render_report(rep, now_ts=2 * DAY))

    def test_no_warning_when_fresh(self):
        rep = lp_stats.compute_report([snap(0, 1.0, pos0=1, pos1=1)])
        self.assertNotIn("не обновлялась", lp_stats.render_report(rep, now_ts=3600))

    def test_counts_snapshot_errors(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            {"ts": 10, "action": "snapshot_error", "error": "rpc"},
        ])
        self.assertEqual(rep["snapshot_errors"], 1)
        self.assertIn("снимок не записался", lp_stats.render_report(rep))


def flow(ts, price, d0=0.0, d1=0.0):
    return {"ts": ts, "action": "capital_flow", "price": price, "d0": d0, "d1": d1}


class TestCapitalFlows(unittest.TestCase):
    def test_deposit_is_not_profit(self):
        # оператор докинул 1 WBNB, бот вложил его в позицию — разница с HODL ноль
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            flow(DAY / 2, 1.0, d1=1.0),
            snap(DAY, 1.0, pos0=1.5, pos1=1.5),
        ])
        self.assertAlmostEqual(rep["diff"], 0.0)
        self.assertAlmostEqual(rep["net_flow"], 1.0)
        self.assertEqual(rep["flows"], 1)

    def test_withdrawal_is_not_loss(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=2, pos1=2),
            flow(DAY / 2, 1.0, d0=-1.0),
            snap(DAY, 1.0, pos0=1, pos1=2),
        ])
        self.assertAlmostEqual(rep["diff"], 0.0)
        self.assertAlmostEqual(rep["net_flow"], -1.0)

    def test_deposited_tokens_follow_price_in_hodl(self):
        # докинули 1 ZEC при цене 1, цена стала 2: HODL держал бы 2 ZEC + 1 WBNB
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            flow(DAY / 2, 1.0, d0=1.0),
            snap(DAY, 2.0, pos0=2, pos1=1),
        ])
        self.assertAlmostEqual(rep["hodl_value"], 5.0)
        self.assertAlmostEqual(rep["diff"], 0.0)

    def test_flows_before_baseline_ignored(self):
        rep = lp_stats.compute_report([
            flow(0, 1.0, d1=5.0),
            snap(DAY, 1.0, pos0=1, pos1=1),
            snap(2 * DAY, 1.0, pos0=1, pos1=1),
        ])
        self.assertEqual(rep["flows"], 0)

    def test_render_shows_flows(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            flow(DAY / 2, 1.0, d1=1.0),
            snap(DAY, 1.0, pos0=1.5, pos1=1.5),
        ])
        self.assertIn("пополнения", lp_stats.render_report(rep))

    def test_gaps_in_flow_check_are_reported(self):
        rep = lp_stats.compute_report([
            snap(0, 1.0, pos0=1, pos1=1),
            {"ts": 10, "action": "flow_check_skipped"},
            snap(DAY, 1.0, pos0=1, pos1=1),
        ])
        self.assertEqual(rep["flow_gaps"], 1)
        self.assertIn("разрыв", lp_stats.render_report(rep))


if __name__ == "__main__":
    unittest.main()
