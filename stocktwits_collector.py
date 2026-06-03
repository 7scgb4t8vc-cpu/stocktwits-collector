Run python stocktwits_collector.py
/home/runner/work/stocktwits-collector/stocktwits-collector/stocktwits_collector.py:254: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
  timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

═══════════════════════════════════════════════════════
StockTwits + FinViz Collector — 2026-06-03 00:30 UTC
═══════════════════════════════════════════════════════

Fetching trending stocks...
  Candidates: BTC.X, MRVL, QQQ, SPY, MSTR, ETH.X, PANW, XRP.X, AVGO, SOXS, BB, MSFT, BMNR, USO, ADA.X

Validating against FinViz (need 10)...
  Checking BTC.X... ✗ Not on FinViz, skipping.
  Checking MRVL... ✓ Price=290.79
  Checking QQQ... ✓ Price=746.16
  Checking SPY... ✓ Price=759.57
  Checking MSTR... ✓ Price=136.08
  Checking ETH.X... ✗ Not on FinViz, skipping.
  Checking PANW... ✓ Price=297.18
  Checking XRP.X... ✗ Not on FinViz, skipping.
  Checking AVGO... ✓ Price=481.57
  Checking SOXS... ✓ Price=5.17
  Checking BB... ✓ Price=10.32
  Checking MSFT... ✓ Price=441.31
  Checking BMNR... ✓ Price=17.97

  Valid stocks (10): MRVL, QQQ, SPY, MSTR, PANW, AVGO, SOXS, BB, MSFT, BMNR

Collecting StockTwits messages...
  [MRVL] Fetching messages (since_id=None)...
  [MRVL] 10 new messages.
  [QQQ] Fetching messages (since_id=None)...
  [QQQ] 10 new messages.
  [SPY] Fetching messages (since_id=654978868)...
  [SPY] 10 new messages.
  [MSTR] Fetching messages (since_id=654990010)...
  [MSTR] 10 new messages.
  [PANW] Fetching messages (since_id=655178835)...
  [PANW] 10 new messages.
  [AVGO] Fetching messages (since_id=655044085)...
  [AVGO] 10 new messages.
  [SOXS] Fetching messages (since_id=None)...
  [SOXS] 10 new messages.
  [BB] Fetching messages (since_id=655178790)...
  [BB] 10 new messages.
  [MSFT] Fetching messages (since_id=655043484)...
  [MSFT] 10 new messages.
  [BMNR] Fetching messages (since_id=None)...
  [BMNR] 10 new messages.

✓ 100 new StockTwits messages appended.
✓ 10 FinViz rows appended.

Updating ticker mention frequency...
  Updated data/frequency.csv — 42 symbols tracked.

Exporting to Excel...
  Saved Excel workbook → data/stocktwits_data.xlsx

✓ All done!
