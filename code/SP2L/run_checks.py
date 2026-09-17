"""One-shot verification: unit tests plus a CLI audit run, logged as UTF-8."""
from io import StringIO
from pathlib import Path
import contextlib
import sys
import unittest

HERE = Path(__file__).parent
LOG = Path(r"C:\Users\Sina\AppData\Local\Temp\sp2l-verify.log")
AUDIT_DIR = Path(r"C:\Users\Sina\AppData\Local\Temp\sp2l-audit-cli")

lines = []

# 1) Offline unit tests
from test_sp2l_backtest import BacktestTests  # noqa: E402

suite = unittest.defaultTestLoader.loadTestsFromTestCase(BacktestTests)
result = unittest.TextTestRunner(stream=StringIO(), verbosity=2).run(suite)
lines.append(f"UNIT TESTS: ran={result.testsRun} failures={len(result.failures)} "
             f"errors={len(result.errors)}")
for failure in result.failures + result.errors:
    lines.append("FAILED: " + failure[0].id())
    lines.append(failure[1].strip().splitlines()[-1])

# 2) CLI audit run on real MT5 M1 history
from SP2L_Advanced_Backtest import main  # noqa: E402

buffer = StringIO()
with contextlib.redirect_stdout(buffer):
    code = main(["--timeframes", "M1", "--bars", "2000",
                 "--audit-dir", str(AUDIT_DIR)])
lines.append(f"CLI EXIT: {code}")
lines.append("CLI OUTPUT:")
lines.extend(buffer.getvalue().strip().splitlines())
report = AUDIT_DIR / "M1" / "report.html"
lines.append(f"REPORT EXISTS: {report.exists()} size={report.stat().st_size if report.exists() else 0}")
import pandas as pd  # noqa: E402
signals = pd.read_csv(AUDIT_DIR / "M1" / "signals.csv")
candles = pd.read_csv(AUDIT_DIR / "M1" / "signal_candles.csv")
events = pd.read_csv(AUDIT_DIR / "M1" / "events.csv")
lines.append(f"AUDIT CONTENT: signals={len(signals)} candles={len(candles)} events={len(events)}")
lines.append(f"STATUS COUNTS: {signals.status.value_counts().to_dict()}")
lines.append(f"EVENT COUNTS: {events.event.value_counts().to_dict()}")
ok = result.wasSuccessful() and code == 0 and report.exists() and len(signals) > 0
lines.append("OVERALL: " + ("PASS" if ok else "CHECK OUTPUT ABOVE"))
LOG.write_text("\n".join(lines), encoding="utf-8")
print("OVERALL: " + ("PASS" if ok else "FAIL"))
