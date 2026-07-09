# Apollo Simulation Stress Mode

Apollo stress mode is on by default. It is a high-variance, paper-only sandbox for seeing dynamics on the `$100` simulated account without changing production paper-trading safety.

## Defaults

- `APOLLO_SIM_STRESS_MODE=1`
- `APOLLO_SIM_STRESS_RISK_PCT=0.25`
- `APOLLO_SIM_STRESS_MAX_NOTIONAL_PCT=1.0`
- `APOLLO_SIM_STRESS_MAX_OPEN_POSITIONS=3`
- `APOLLO_SIM_STRESS_ALLOW_FRACTIONAL=1`

Stress mode always remains simulation-only:

- `live_trade_execution=false`
- `human_approval_required=true`
- provider forced to local `paper_sim`
- no production readiness credit

## Expected Behavior

- Production paper trading still obeys the risk ladder and can remain in `tier_0_hold`.
- Stress sandbox can still produce a nonzero simulated allocation while production allocation is `$0.00`.
- High-priced tickers can use fractional simulated shares.
- Stress reports are written under `Apollo/reports/stress_simulation/<run_id>/report.md`.
- Stress report capture is submitted to Sky/FTP as `test_type=apollo_stress_simulation` and `capability=simulation_sandbox_evidence`.

## Test Layout

Primary test file:

```text
Apollo/tests/test_apollo_stress_mode.py
```

Covered scenarios:

- Stress mode defaults enabled when no env override is present.
- Stress sizing supports fractional shares for a `$100` account and high-priced ticker.
- Production `tier_0_hold` blocks normal deployment but does not block the stress sandbox plan.
- Trade cycle marks a `stress_probe_ready` candidate without marking it production trade-ready.
- Simulation execution forces stress mode to local `paper_sim` even if a broker provider is configured.

Recommended targeted command:

```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
python -m pytest tests\test_apollo_stress_mode.py
```

Recommended regression command:

```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
python -m pytest tests\test_apollo_stress_mode.py tests\test_trade_cycle.py tests\test_swing_simulation_account.py tests\test_simulation_execution.py tests\test_daily_report.py tests\test_nightly_high_risk_homework.py
```

## Premarket Simulation Readiness Gate

Before letting the high-risk sandbox run unattended, run:

```powershell
cd C:\Users\blyth\Desktop\Engineering
py -3 -m Apollo.premarket_sim_readiness
```

Or through the watchdog launcher:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_premarket_sim_readiness.ps1
```

The gate checks:

- Apollo `/health` is reachable and the financial corpus is ready.
- Swing provider data is fresh enough for premarket simulation review.
- The daily report exists/opens for the selected date.
- Stress mode is still forced to `paper_sim`, `paper_only=true`, `live_trade_execution=false`, `human_approval_required=true`, and `production_readiness_credit=false`.
- The aggressive probe suite still passes.

Reports are written under:

```text
Apollo/reports/premarket_sim_readiness/
```

Passing this gate means Apollo is ready for high-variance simulation-only probes. It does not approve Alpaca, paper broker order submission, or live trading.

## Interpretation

Passing stress tests prove that Apollo can show larger simulated dynamics safely. They do not prove Apollo is ready for live trading or higher production paper-risk tiers.
