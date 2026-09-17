"""Offline money-account tests. No broker connection required."""
import contextlib
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from SP2L_Money_Backtest import MoneySettings, main, simulate_money, size_trade, validate_money
from test_sp2l_backtest import history


def trade(index=0, outcome='TP', direction='BUY'):
    start = pd.Timestamp('2026-01-01', tz='UTC') + pd.Timedelta(minutes=index * 3)
    return dict(direction=direction, entry_time=start,
                exit_time=start + pd.Timedelta(minutes=1), entry=100,
                sl=99 if direction == 'BUY' else 101,
                tp=101 if direction == 'BUY' else 99, outcome=outcome)


class MoneyTests(unittest.TestCase):
    def test_exact_compounding(self):
        settings = MoneySettings(volume_step=0.0001, volume_min=0.0001)
        result = simulate_money([trade(), trade(1, 'SL')], settings)
        self.assertAlmostEqual(result['final_cash'], 999.60)
        self.assertAlmostEqual(result['rows'][0]['volume'], 0.2)
        self.assertAlmostEqual(result['rows'][1]['volume'], 0.204)
        self.assertAlmostEqual(result['rows'][1]['requested_risk'], 20.40)
        self.assertAlmostEqual(result['max_drawdown'], 20.40)
        self.assertAlmostEqual(result['max_drawdown_pct'], 2)

    def test_floor_and_maximum(self):
        settings = MoneySettings(volume_max=0.1)
        self.assertEqual(size_trade(20, 100, settings), 0.1)
        settings = MoneySettings()
        self.assertEqual(size_trade(20, 300, settings), 0.06)
        for loss in (0.1, 7, 30, 100, 301, 999, 2001):
            self.assertLessEqual(size_trade(20, loss, settings) * loss, 20 + 1e-10)

    def test_skip_and_open(self):
        skipped = simulate_money([trade(outcome='SL')], MoneySettings(loss_value=3000))
        self.assertEqual(skipped['final_cash'], 1000)
        self.assertEqual(skipped['rows'][0]['outcome'], 'SKIPPED')
        opened = simulate_money([trade(outcome='OPEN')], MoneySettings())
        self.assertEqual(opened['final_cash'], 1000)
        self.assertEqual(opened['rows'][0]['pnl'], 0)

    def test_sell_and_asymmetric_values(self):
        result = simulate_money([trade(direction='SELL')], MoneySettings(profit_value=90))
        self.assertAlmostEqual(result['final_cash'], 1018)

    def test_invalid_inputs_and_overlap(self):
        for key, value in [('initial_cash', 0), ('risk_pct', 101),
                           ('loss_value', float('nan')), ('volume_step', float('inf'))]:
            with self.assertRaises(ValueError):
                validate_money(replace(MoneySettings(), **{key: value}))
        with self.assertRaises(ValueError):
            simulate_money([trade(), trade()], MoneySettings())
        self.assertEqual(simulate_money([], MoneySettings())['final_cash'], 1000)

    def test_csv_cli_ledger(self):
        data = pd.concat([history().iloc[[0]]] * 495 + [
            history([(110, 112, 106, 111), (111, 116, 110, 115)])])
        data.index = pd.date_range('2026-01-01', periods=len(data), freq='min', tz='UTC')
        with tempfile.TemporaryDirectory() as folder:
            source, ledger = Path(folder) / 'input.csv', Path(folder) / 'ledger.csv'
            data.to_csv(source, index_label='time')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--csv', str(source), '--point', '0.01',
                             '--value-per-price', '100', '--trades-csv', str(ledger)])
            self.assertEqual(code, 0)
            self.assertIn('Final realized cash: $1,016.00', output.getvalue())
            self.assertIn('Growth: +1.60%', output.getvalue())
            row = pd.read_csv(ledger).iloc[0]
            self.assertEqual(row.volume, 0.02)
            self.assertEqual(row.requested_risk, 20)
            self.assertEqual(row.actual_risk, 16)
            self.assertEqual(row.cash_after, 1016)


if __name__ == '__main__':
    unittest.main(verbosity=2)
