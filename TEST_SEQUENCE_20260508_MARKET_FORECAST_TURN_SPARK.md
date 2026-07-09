# TEST_SEQUENCE_20260508_MARKET_FORECAST_TURN_SPARK

Purpose: verify Apollo can combine news event impact, event-aware swing study, next-session normalization, optional OCR97 document handling, and simulated future-turn reporting without creating broker orders.

## Commands

Run from `C:\Users\blyth\Desktop\Engineering\Apollo`:

```powershell
python -m py_compile market_forecast_turn.py event_impact.py swing_study.py app.py mcp_routes.py apollo_tools\mcp_event_impact.py
python -m pytest tests\test_market_forecast_turn.py tests\test_event_impact.py tests\test_swing_study.py tests\test_mcp_routes.py -q
```

Optional live local smoke after Apollo is running on `5031`:

```powershell
$body = @{
  tickers = "SPY,QQQ,NVDA,AMD,AVGO,MSFT,AMZN"
  as_of = "2026-05-08T22:00:00-05:00"
  simulate_at = "2026-05-09T12:00:00-05:00"
  max_results = 4
  write_artifacts = $true
} | ConvertTo-Json

Invoke-RestMethod http://127.0.0.1:5031/admin/market_forecast_turn/run `
  -Method Post `
  -Body $body `
  -ContentType "application/json" `
  -TimeoutSec 180 |
  Select-Object ok, run_id, requested_date, next_trading_session, session_adjustment, broker_readiness |
  ConvertTo-Json -Depth 6
```

## Expected

- Tests pass.
- `requested_date` for Saturday normalizes to next trading session Monday.
- `simulation.status` is `weekend_monitoring` for the Saturday case.
- `ocr97.mode` is `skipped_no_documents` unless `ocr_documents` are supplied.
- `broker_readiness.live_trade_execution` remains `false`.
- `broker_readiness.broker_order_created` remains `false`.

