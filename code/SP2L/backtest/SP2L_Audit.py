"""Offline HTML/CSV evidence for manual SP2L review."""
from dataclasses import asdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path

import pandas as pd


class SignalAudit:
    def __init__(self):
        self.signals = []
        self.candles = []
        self.events = []

    def signal(self, window, pending, settings, duration):
        signal_id = len(self.signals) + 1
        entry, sl = pending['entry'], pending['sl']
        risk = entry - sl if pending['direction'] == 'BUY' else sl - entry
        self.signals.append(dict(
            signal_id=signal_id, direction=pending['direction'],
            signal_bar_utc=window.index[-1], decision_utc=window.index[-1] + duration,
            initial_entry=entry, initial_sl=sl,
            initial_tp=round(entry + settings.tp_r * risk if pending['direction'] == 'BUY'
                             else entry - settings.tp_r * risk, settings.digits),
            signal_ema=window.iloc[-1]['EMA'], signal_adx=window.iloc[-1]['ADX'],
            status='DETECTED',
        ))
        roles = ['context', 'before_spike', 'spike', 'after_spike', 'signal']
        for role, (timestamp, row) in zip(roles, window.iloc[-5:].iterrows()):
            self.candles.append(dict(signal_id=signal_id, role=role, bar_utc=timestamp,
                                     **{key: float(row[key]) for key in
                                        ('open', 'high', 'low', 'close', 'spread')}))
        return signal_id

    def record(self, setup, timestamp, event, reason='', **extra):
        if setup is None or 'signal_id' not in setup:
            return
        signal_id = setup['signal_id']
        self.events.append(dict(signal_id=signal_id, bar_utc=timestamp,
                                event=event, reason=reason,
                                entry=setup.get('entry'), sl=setup.get('sl'),
                                tp=setup.get('tp'), **extra))
        if event in ('REPLACED', 'CANCELLED', 'FILLED', 'TP', 'SL', 'OPEN', 'UNFILLED'):
            self.signals[signal_id - 1]['status'] = event

    def export(self, directory, data, trades, settings, symbol, timeframe, offset_hours):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        frames = {
            'signals': pd.DataFrame(self.signals, columns=[
                'signal_id', 'direction', 'signal_bar_utc', 'decision_utc', 'initial_entry',
                'initial_sl', 'initial_tp', 'signal_ema', 'signal_adx', 'status']),
            'signal_candles': pd.DataFrame(self.candles, columns=[
                'signal_id', 'role', 'bar_utc', 'open', 'high', 'low', 'close', 'spread']),
            'events': pd.DataFrame(self.events),
            'trades': pd.DataFrame(trades),
        }
        for name in ('events', 'trades'):
            if frames[name].empty:
                frames[name] = pd.DataFrame(columns=['signal_id', 'bar_utc', 'event'] if name == 'events'
                                           else ['signal_id', 'entry_time', 'exit_time', 'outcome'])
        for frame in frames.values():
            for column in list(frame.columns):
                if column.endswith('_utc') or column in ('setup_time', 'entry_time', 'exit_time'):
                    values = pd.to_datetime(frame[column], utc=True)
                    if offset_hours is not None:
                        frame[column + '_chart_label'] = (
                            values + pd.Timedelta(hours=offset_hours)).dt.strftime('%Y.%m.%d %H:%M:%S')
        for name, frame in frames.items():
            frame.to_csv(directory / f'{name}.csv', index=False)
        data.to_csv(directory / 'history.csv', index_label='time')
        root = Path(__file__).parent
        metadata = dict(
            symbol=symbol, timeframe=timeframe, created_utc=pd.Timestamp.now(tz='UTC').isoformat(),
            settings=asdict(settings), chart_offset_hours=offset_hours,
            first_bar_utc=str(data.index[0]), last_bar_utc=str(data.index[-1]), bars=len(data),
            signal_scope='Only setups scanned while flat and outside cooldown, after warmup.',
            time_note='Entry/exit times identify candles, NOT exact tick execution times.',
            files_sha256={path.name: sha256(path.read_bytes()).hexdigest()
                          for path in [directory / 'history.csv', root / 'SP2L_Advanced_Bot.py',
                                       root / 'SP2L_Advanced_Backtest.py', Path(__file__)]},
        )
        (directory / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        return self.write_html(directory, frames, symbol, timeframe)

    def write_html(self, directory, frames, symbol, timeframe):
        title = f'{symbol} {timeframe} signal audit'
        parts = ['<!doctype html><html><head><meta charset="utf-8">',
                 f'<title>{escape(title)}</title>',
                 '<style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;'
                 'font-size:13px}td,th{border:1px solid #bbb;padding:6px;white-space:nowrap}'
                 'th{background:#eee}section{overflow:auto;margin-bottom:24px}'
                 'summary{cursor:pointer;font-weight:bold;padding:10px}</style></head><body>',
                 f'<h1>{escape(title)}</h1>',
                 '<p><b>SIMULATED trades, not broker executions.</b> Decisions occur at signal-bar close. '
                 'Entry/exit timestamps identify whole candles. OHLC cannot prove intrabar sequencing.</p>',
                 '<p>UTC is authoritative. Optional chart labels use your fixed offset, not automatic '
                 'broker/DST detection. Verify the offset against an identical OHLC candle in MT5.</p>',
                 '<p>Only setups scanned after warmup while flat/outside cooldown are recorded. '
                 'No signal means no detected setup, not a failed export.</p>',
                 '<p>Open the same broker symbol/timeframe in MT5, disable Auto Scroll, navigate to '
                 'the signal time, and use the Data Window (Ctrl+D) to compare OHLC. Check each '
                 'event and entry/SL/TP against subsequent candles. Both-level touches and fill-bar '
                 'uncertainty require tick history. Buy fills use estimated ask; sell exits use ask.</p>',
                 '<p>Files: ' + ' | '.join(f'<a href="{name}.csv">{name}.csv</a>' for name in
                    ('signals', 'signal_candles', 'events', 'trades', 'history')) +
                 ' | <a href="metadata.json">settings / source hashes</a></p>',
                 '<h2>Signals</h2><section>', frames['signals'].to_html(index=False, escape=True), '</section>']
        for signal in self.signals:
            sid = signal['signal_id']
            parts.append(f'<details><summary>#{sid} {escape(signal["direction"])} '
                         f'{escape(str(signal["signal_bar_utc"]))} — {escape(signal["status"])}</summary>')
            for name in ('signal_candles', 'events', 'trades'):
                frame = frames[name]
                subset = frame.loc[frame.signal_id == sid]
                parts.extend([f'<h3>{name}</h3><section>',
                              subset.to_html(index=False, escape=True), '</section>'])
            parts.append('</details>')
        parts.append('</body></html>')
        report = directory / 'report.html'
        report.write_text('\n'.join(parts), encoding='utf-8')
        return report
