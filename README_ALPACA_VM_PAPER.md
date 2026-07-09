# Apollo Alpaca VM Paper Trading Notes

Last checked: 2026-05-07 local / 2026-05-08 UTC

Apollo should treat Alpaca as a paper-trading broker integration candidate only. Live order execution, funding, bank linking, and live account workflows remain blocked unless the operator explicitly changes policy later.

## Current Auth State

Aegis verified that the VM Chrome profile at `/root/.config/aegis-alpaca-chrome` can load an authenticated Alpaca page at `https://app.alpaca.markets/brokerage/new-account`.

Important distinction: Chrome password-manager storage did not show an Alpaca saved-password entry. What is currently saved is the VM browser session/auth state, including persistent Cognito auth cookies/tokens. Apollo should rely on Aegis to open the VM session for dashboard navigation, not assume it can recover the raw login after the session expires.

## Page Map

Machine-readable page map:

`C:\Users\blyth\Desktop\Engineering\Apollo\config\alpaca_vm_page_map.json`

Observed paper account shell controls:

- Account switcher: visible text starts with `Paper Trading`; selector hint `[data-testid='account-switcher-button']`.
- Main nav: `Home`, `Account`, `Positions`, `Orders`, `Activities`, `Balances`, `Configure`, `Alpaca Connect`, `Plans & Features`, `API`, `Community`, `Support`, `Legal`.
- Search input: placeholder `Search by symbol...`.
- Current blocker card: `Secure Your Account`; MFA/authenticator activation is required and must be handled by the operator.

## Apollo Boundaries

Allowed:

- Ask Aegis to open/focus Alpaca in the VM through `POST /forgeclaw/vm/browser/open_url`.
- Navigate to paper account API/settings pages.
- Read paper positions, paper orders, paper activities, and API readiness state.
- Use paper API credentials only after operator approval and only from approved secret storage.

Blocked:

- Live trades or broker orders.
- Funding, bank linking, deposits, withdrawals, or live account enablement.
- MFA setup, KYC, personal identity fields, or account agreements without the operator controlling the step.
- Credential extraction into logs, reports, markdown, RAG, screenshots, or chat.

## Paper Env Targets

Use these names only after paper credentials are approved for storage:

- `APOLLO_BROKER_PROVIDER=alpaca`
- `APOLLO_ALPACA_ENV=paper`
- `APCA_API_BASE_URL=https://paper-api.alpaca.markets`
- `APCA_API_KEY_ID=<paper key id>`
- `APCA_API_SECRET_KEY=<paper secret>`

The first API test must be read-only provider/account validation. Do not create orders until paper-only execution is explicitly wired and separately verified.
