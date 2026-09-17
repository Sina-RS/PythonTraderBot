"""Deterministic offline checks; no MT5 or Telegram connection required."""
import contextlib
import inspect
import io
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from SP2L_Advanced_Backtest import (
    Settings, exit_result, load_strategy, main, prepare_data, run_backtest, summarize,
)


def history(extra=()):
    rows = [(99, 100, 98, 99), (100, 102, 99, 101),
            (102, 108, 101, 107), (108, 110, 107, 109),
            (109, 111, 108, 110)] + list(extra)
    return pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'],
                        index=pd.date_range('2026-01-01', periods=len(rows), freq='min', tz='UTC'))


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(number_of_data=5, use_ema_filter=False,
                                 use_range_filter=False, use_trend_filter=False)

    def test_import_is_the_adjacent_backtest(self):
        expected = Path(__file__).with_name('SP2L_Advanced_Backtest.py').resolve()
        self.assertEqual(Path(inspect.getfile(run_backtest)).resolve(), expected)

    def test_original_buy_and_sell_signals(self):
        strategy = load_strategy(self.settings)
        data = history()
        self.assertTrue(strategy.detect_buy_setup(data))
        self.assertFalse(strategy.detect_sell_setup(data))
        mirrored = 220 - data
        mirrored['high'], mirrored['low'] = 220 - data.low, 220 - data.high
        self.assertTrue(strategy.detect_sell_setup(mirrored))

    def test_buy_tp_and_next_bar_entry(self):
        data = history([(110, 112, 106, 111), (111, 116, 110, 115)])
        trades = run_backtest(data, self.settings)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['entry_time'], data.index[5])
        self.assertEqual(trades[0]['entry'], 107)
        self.assertEqual(trades[0]['sl'], 99)
        self.assertEqual(trades[0]['tp'], 115)
        self.assertEqual(summarize(trades)['win_rate'], 100)

    def test_sell_tp(self):
        data = history([(110, 112, 106, 111), (111, 116, 110, 115)])
        mirrored = 220 - data
        mirrored['high'], mirrored['low'] = 220 - data.low, 220 - data.high
        trades = run_backtest(mirrored, self.settings)
        self.assertEqual(trades[0]['direction'], 'SELL')
        self.assertEqual(trades[0]['outcome'], 'TP')

    def test_stop_on_fill_bar_not_cancelled_retroactively(self):
        trades = run_backtest(history([(110, 116, 98, 100)]), self.settings)
        self.assertEqual(summarize(trades)['sl'], 1)

    def test_open_excluded(self):
        trades = run_backtest(history([(110, 112, 106, 111)]), self.settings)
        self.assertEqual(summarize(trades)['open'], 1)
        self.assertIsNone(summarize(trades)['win_rate'])

    def test_unfilled_not_counted(self):
        self.assertEqual(run_backtest(history([(110, 112, 109, 111)]), self.settings), [])

    def test_trailing_only_affects_next_bar(self):
        trades = run_backtest(history([(110, 112, 109, 111),
                                       (111, 113, 108, 112)]), self.settings)
        self.assertEqual(trades[0]['entry'], 109)
        self.assertEqual(trades[0]['tp'], 119)

    def test_fill_bar_high_is_not_automatically_a_win(self):
        trades = run_backtest(history([(116, 117, 106, 110)]), self.settings)
        self.assertEqual(trades[0]['outcome'], 'OPEN')

    def test_spread_prevents_buy_fill(self):
        data = history([(110, 112, 106, 111)])
        data['spread'] = 200
        self.assertEqual(run_backtest(data, self.settings), [])

    def test_existing_position_ambiguity_and_gap(self):
        trade = dict(direction='BUY', entry=100, sl=99, tp=101)
        self.assertEqual(exit_result(trade, pd.Series(dict(open=100, high=102, low=98, close=100)), 0), 'SL')
        self.assertEqual(exit_result(trade, pd.Series(dict(open=102, high=103, low=98, close=100)), 0), 'TP')
        trade = dict(direction='SELL', entry=100, sl=101, tp=99)
        self.assertEqual(exit_result(trade, pd.Series(dict(open=100, high=100.5, low=99.5, close=100)), 1), 'SL')

    def test_invalid_and_short_data(self):
        with self.assertRaises(ValueError):
            run_backtest(history(), self.settings)
        data = history()
        data.iloc[0, data.columns.get_loc('high')] = 1
        with self.assertRaises(ValueError):
            prepare_data(data)
        with self.assertRaises(ValueError):
            prepare_data(pd.concat([history(), history()]))

    def test_audit_records_without_changing_trades(self):
        from SP2L_Audit import SignalAudit
        data = history([(110, 112, 106, 111), (111, 116, 110, 115)])
        audit = SignalAudit()
        audited = run_backtest(data, self.settings, audit=audit)
        plain = run_backtest(data, self.settings)
        fields = ('direction', 'setup_time', 'entry_time', 'exit_time',
                  'entry', 'sl', 'tp', 'outcome')
        self.assertEqual([{k: t.get(k) for k in fields} for t in audited],
                         [{k: t.get(k) for k in fields} for t in plain])
        self.assertEqual(len(audit.signals), 1)
        self.assertEqual(audit.signals[0]['direction'], 'BUY')
        self.assertEqual(audit.signals[0]['status'], 'TP')
        self.assertEqual(len(audit.candles), 5)
        self.assertEqual([candle['role'] for candle in audit.candles[-1:]], ['signal'])
        self.assertTrue(any(event['event'] == 'DETECTED' for event in audit.events))
        self.assertTrue(any(event['event'] == 'FILLED' for event in audit.events))
        with tempfile.TemporaryDirectory() as directory:
            report = audit.export(Path(directory) / 'M1', data, audited, self.settings,
                                  'XAUUSD', 'M1', 3.0)
            self.assertTrue(report.exists())
            for name in ('signals', 'signal_candles', 'events', 'trades', 'history'):
                self.assertTrue((report.parent / f'{name}.csv').exists())
            exported = pd.read_csv(report.parent / 'signals.csv')
            self.assertEqual(exported.status.tolist(), ['TP'])
            labelled = pd.read_csv(report.parent / 'signal_candles.csv')
            self.assertIn('bar_utc_chart_label', labelled.columns)
            self.assertEqual(labelled.signal_id.tolist(), [1] * 5)
            self.assertEqual(audit.signals[0]['decision_utc'], data.index[5])

    def test_audit_replacement_and_unfilled_statuses(self):
        from SP2L_Audit import SignalAudit
        # Hand-built series: BUY signal at bar 4 (entry 106.5, SL 99); the
        # limit trails up but never fills; a second BUY signal at bar 6
        # replaces it while it is still valid.
        rows = [(99, 100, 98, 99), (100, 102, 99, 101), (102, 108, 101, 107),
                (106.8, 107.5, 106.5, 107.2), (107.5, 110, 107.2, 109.8),
                (108.8, 110.5, 108.7, 110.2), (110.3, 110.6, 110.1, 110.4)]
        data = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'],
                            index=pd.date_range('2026-01-01', periods=7, freq='min', tz='UTC'))
        audit = SignalAudit()
        run_backtest(data, self.settings, audit=audit)
        self.assertEqual([signal['status'] for signal in audit.signals],
                         ['REPLACED', 'UNFILLED'])
        self.assertTrue(any(event['event'] == 'REPLACED' for event in audit.events))
        audit = SignalAudit()
        run_backtest(history([(110, 112, 109, 111)]), self.settings, audit=audit)
        self.assertEqual(audit.signals[0]['status'], 'UNFILLED')

    def test_default_filters_run_and_csv_cli(self):
        data = pd.concat([history().iloc[[0]]] * 495 + [history([(110, 112, 106, 111), (111, 116, 110, 115)])])
        data.index = pd.date_range('2026-01-01', periods=len(data), freq='min', tz='UTC')
        self.assertEqual(run_backtest(data)[0]['outcome'], 'TP')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'rates.csv'
            output = Path(directory) / 'trades.csv'
            data.to_csv(source, index_label='time')
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                result = main(['--csv', str(source), '--timeframes', 'M1',
                               '--point', '0.01', '--trades-csv', str(output)])
            self.assertEqual(result, 0)
            self.assertEqual(text.getvalue(),
                             'Closed-bar approximation; SL-first ambiguity; open trades excluded.\n'
                             'XAUUSD M1: 502 bars, 2026-01-01 00:00:00+00:00 to 2026-01-01 08:21:00+00:00\n'
                             'M1: TP=1 | SL=0 | Win rate=100.00% | Open/excluded=0\n')
            self.assertEqual(pd.read_csv(output).outcome.tolist(), ['TP'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
