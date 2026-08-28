# Alpaca Broker API — Reference Notes

Compiled from `docs.alpaca.markets` (Broker API doc tree) while scoping how to test account
creation in Alpaca's **sandbox** environment. Kept here for reference in case the project ever
needs to manage multiple end-customer accounts (e.g. a hosted demo with its own signup flow).

> **Scope note:** the hackathon itself (see [hackathon.md](hackathon.md) / [plan.md](plan.md))
> is built on the plain **Trading API** against a single personal **paper trading account** —
> not the Broker API. Everything below is Broker-API-specific (multi-account brokerage
> infrastructure) and is *not* required for the competition submission. It's saved here purely
> as a reference in case that changes.

Sources swept: `about-broker-api`, `getting-started-with-broker-api`,
`integration-setup-with-alpaca`, `broker-api-faq`, `authentication`, `credential-management`,
`account-opening`, `accounts-statuses`, `data-validations`, `funding-accounts`, `ach-funding`,
`funding-via-journals`, `instant-funding`, `brokerapi-trading`, `sse-events`,
`account-status-events-for-kycaas`, `activity-sse`, `statements-and-confirms`.

Enterprise-niche sections were intentionally skipped as out of scope: IRA, custodial,
international accounts, tokenization, fixed income, ACATs, options margin rules.

---

## 1. Environments & base URLs

| Account type | Trading / Broker API | Market data API | OAuth service |
|---|---|---|---|
| Live (single trader) | `api.alpaca.markets` | `data.alpaca.markets` | — |
| Paper (single trader) | `paper-api.alpaca.markets` | `data.alpaca.markets` | — |
| **Live broker partner** | `broker-api.alpaca.markets` | `data.alpaca.markets` | `authx.alpaca.markets` |
| **Sandbox broker partner** | `broker-api.sandbox.alpaca.markets` | `data.sandbox.alpaca.markets` | `authx.sandbox.alpaca.markets` |

If a request authenticates successfully against `broker-api.sandbox.alpaca.markets`, you're
confirmed in the dev/sandbox environment — sandbox keys don't work against the production host
and vice versa.

## 2. Authentication & credentials

### Legacy auth
- **HTTP Basic** — key ID as username, secret as password: `Authorization: Basic base64(KEY:SECRET)`
- **Custom headers** — `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`

Same credentials work for Market Data API calls. Every response carries an `X-Request-ID`
header — save it; it can't be retrieved after the fact for a support ticket.

### Client Credentials (OAuth2) — newer, broker-oriented
| | |
|---|---|
| Token endpoint | `POST authx.alpaca.markets/v1/oauth2/token` |
| Token validity | 15 minutes — reuse, don't fetch a new one per call |
| Method 1 | client ID + client secret in the request body (`client_secret_post` only) |
| Method 2 | client ID + signed JWT assertion (RFC 7523) — private key never leaves your system |
| Resulting header | `Authorization: Bearer <token>` |

Not yet available for the plain Trading API. Live and sandbox credentials are entirely separate.

### Credential management (BrokerDash)
- Any user role can **view** existing keys; only **superusers** can create new ones.
- Expirations: never, 1 week, 30 days, 90 days, 6 months, 1 year, or custom.
- Access presets: **Read only**, **Full access**, or **Custom** per scope (Accounts, Funding,
  Admin, Crypto, Rebalancing, Trading, Journaling, Data, Reporting, SSE events).
- *Gap in the docs:* no explicit key-rotation procedure or max-keys-per-org number — check the
  BrokerDash UI directly if needed.

## 3. Sandbox vs. production — the KYC question

**Getting sandbox access itself: no KYC, confirmed.** Signing up for the dashboard and
generating sandbox API keys is free, instant, no identity verification. That's just the
developer/firm.

**Creating a test customer account (the "Individual Account" screen): process is relaxed, data
format is not.**
- *Process is simulated* — sandbox account approval runs "fully automated with account approval
  simulation with test fixtures," described as "same code as live with a few different
  behaviors." No manual-review branch exists in sandbox. You can open unlimited accounts in
  sandbox even under an omnibus setup, where production would normally have Alpaca pre-create
  accounts instead.
- *Field-level validation is not relaxed.* The data-validation rules (name/address formats,
  tax-ID checksums, ASCII-only text, etc. — §6) apply identically in both environments. Nothing
  in the docs says sandbox waives those checks.

**Practical takeaway:** fill the Individual Account form with placeholder-but-correctly-formatted
data — a properly checksummed fake SSN, a real-looking non–PO-Box address, any image file for
the ID upload — and it should auto-approve near-instantly. If it lands on `ACTION_REQUIRED`,
sandbox still wants a document uploaded, but nothing suggests it's verified against a real
registry.

### What else sandbox relaxes vs. production

| Area | Sandbox | Production |
|---|---|---|
| Trading | Simulated engine; real market hours & pricing, no order reaches an exchange | Live execution |
| Funding / transfers | Simplified Transfer API, near-immediate effect, no real bank rails | Full Banks/ACH API, real settlement timing |
| ACH relationship | `QUEUED → APPROVED` | Same states, real bank verification |
| Transfer cash posting | Simulated 10–30 min delay | Real settlement timing |
| Journaling | Approvals simulated, no manual-review gate | Requests over configured limits require manual ops review |
| Firm account | Pre-funded with $50,000 per org, for instant JNLC test funding | Real firm capital |
| Rate limits | "Significantly lower than production" — not meant for load testing | Set per-correspondent by usage |

### Production-only requirements (go-live checklist)
Not needed for sandbox/hackathon work: business entity documents, application
screenshots/video, KYC process documentation (fully-disclosed model), funding process
expectations, and a signed business agreement with Alpaca.

## 4. Account creation

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/v1/accounts` | Create account — Trading/Investing App & RIA path |
| POST | `/v1/accounts/{account_id}/cip` | Submit CIP results — fully-disclosed broker-dealers running their own KYC |
| GET | `/reference/getdocsforaccount` | Retrieve/upload documents for an account |
| PATCH | `/v1/trading-accounts/{account_id}/account-configurations` | Update config (e.g. margin vs. cash) |

### KYC ownership by business model

| Setup | Who does KYC | Approval mechanism |
|---|---|---|
| Trading/Investing App, RIA | You collect it; Alpaca validates | Alpaca's automated process |
| Fully-Disclosed Broker-Dealer | You run your own full KYC | You approve before `POST /v1/accounts`; Alpaca adds a blacklist screen |
| Omnibus | N/A — no account-opening API used in production | Accounts pre-created by Alpaca at go-live |

> *"Upon the POST request, the account status starts from `SUBMITTED` status. Alpaca system
> will run the automatic KYC process asynchronously and update the KYC result as the account
> status."*

### Request sections (fully-disclosed model)
- **Contact** — email, phone, address, city, postal code, state
- **Identity** — name, DOB, tax ID, citizenship, income/net-worth ranges, investment experience, risk tolerance
- **Disclosures** — control-person, politically-exposed, affiliate-status flags
- **Agreements** — customer / options / margin agreement, each with a timestamp and IP address
- **Documents** — identity-verification image, base64-encoded, with MIME type

Response includes: account ID, account number, status (e.g. `APPROVED`), currency (`USD`),
equity, creation timestamp. Alpaca opens all accounts as **margin** accounts by default —
switch to cash (100% buying power) via the account-configurations PATCH endpoint.

## 5. Account statuses

Two parallel status fields exist per account: `status` (equities) and `crypto_status`
(crypto) — they can differ.

| Status | Meaning |
|---|---|
| `INACTIVE` | Not set to trade the given asset class |
| `ONBOARDING` | Created but KYC not yet run — only used with Onfido |
| `SUBMITTED` | Application processing; transitory |
| `SUBMISSION_FAILED` | Creation failed — resolved by Alpaca, no user action |
| `ACTION_REQUIRED` | Document upload required from the user |
| `APPROVAL_PENDING` | Manual internal review — likely no documents needed |
| `APPROVED` | Approved, awaiting active; transitory |
| `REJECTED` | Permanently declined |
| `ACTIVE` | Fully active, ready to trade |
| `ACCOUNT_UPDATED` | Personal info under review; outgoing transfers restricted |
| `ACCOUNT_CLOSED` | No trading or funding permitted |

**`ACCOUNT_UPDATED` in detail:** non-material updates auto-return to `ACTIVE`; material updates
need additional docs (IDs, W-8BEN, address verification, CIP reports) and are processed
manually. Triggered by changes to: `given_name`, `family_name`, `street_address`, `unit`,
`city`, `state`, `postal_code`, `country_of_citizenship`, `employer_name`,
`employment_position`, `is_control_person`, `is_politically_exposed`,
`immediate_family_exposed`, `is_affiliated_exchange_or_finra`.

## 6. Data validation rules

Applies to `POST /v1/accounts` and `PATCH /v1/accounts/:id`. Failures return **422**
(occasionally 400 with code `40010001`). **Identical in sandbox and production** — no
environment-based exceptions documented.

**The load-bearing ones:**
- **ASCII only:** names, address, email, tax ID must be ASCII 32–126. Only plain space (ASCII
  32) counts as whitespace; leading/trailing spaces rejected.
- **Tax ID (US SSN/ITIN):** 9 digits; area (first 3) can't be `000`/`666`; group (middle 2)
  can't be `00`; serial (last 4) can't be `0000`; can't be all-identical or sequential (e.g.
  `123-45-6789`).
- **No PO Boxes** for residential street addresses — case-insensitive match on "PO Box" /
  "Post Office Box" / "P.O. Box" / "Box #".
- **Country codes:** ISO 3166-1 alpha-3 (`USA`, `GBR`, `CAN`…), required on `contact.country`
  and the `identity.country_of_*` fields.
- **Dates:** `YYYY-MM-DD` everywhere, incl. `date_of_birth`.

<details>
<summary>Full field-by-field limits & formats</summary>

**Names**
- `identity.given_name` required for all users; max 50 chars per name component; can't be
  all-digits; no leading/trailing spaces.
- `identity.prefix` max 7 chars, `identity.suffix` max 3 chars.

**Address**
- Street address must be >1 char, not all-digits.
- Postal code: max 12 chars overall. USA: 5–10 chars, first 5 digits-only, required.
  UK/Canada: must match national formats (`SW4 6EH`, `A1A 1A1`).
- Unit/Apt: max 20 chars, designator only (e.g. `Ste 100`) — not a full line.
- City: 2–50 chars (individuals) / 2–100 (entities); not all-digits.
- State: max 50 chars; USA must be 2-letter abbreviation or full name.

**Tax ID (general, all types)**
- Length 2–40 chars, ≥1 digit; letters, digits, dashes, periods, plus signs allowed.
- Rejects all-same/sequential digits and known placeholders (`TIN_NOT_ISSUED`, `xxx-xxx-xxxx`).
- No tax ID available → use `tax_id_type` of `NATIONAL_ID`, `PASSPORT`,
  `PERMANENT_RESIDENT`, `DRIVER_LICENSE`, or `OTHER_GOV_ID` instead.

**Email**
- Max 60 chars after alias-stripping (everything between `+` and `@`); max 100 chars stored.

**Visa (non-US citizens)**
- Allowed `visa_type`: `E1, E2, E3, F1, H1B, TN1, O1, J1, L1, B1, B2, DACA, G4, OTHER` (max 5
  chars).
- `B1`/`B2` → `country_of_tax_residence` must be `USA`.
- Any visa type set → `visa_expiration_date` required (`YYYY-MM-DD`).
- `OTHER` → `visa_type_other_free_text` required, max 50 chars.

**Legal entities (RIA / entity accounts)**
- Required: `legal_name`/`entity_name`, `country_of_incorporation` (+
  `state_of_incorporation` if USA), `date_of_incorporation` (`YYYY-MM-DD`), `entity_type`,
  `funding_source`, `type_of_business`, `contact.country`.
- Any "other" selection requires a matching `*_other_free_text` field, max 50 chars.

**Disclosures (equity accounts, individuals)**
- `disclosures.employment_status`: `EMPLOYED`, `UNEMPLOYED`, `RETIRED`, or `STUDENT`.
- `disclosures.affiliated_firm`: max 100 chars.

**Beneficiaries (e.g. IRA accounts)**
- Sum of all `share_pct` must equal exactly 100; each ≤ 100, ~2 decimal precision.
- Per beneficiary: `given_name`, `family_name`, `date_of_birth`, `tax_id`, `tax_id_type` (same
  tax-ID rules apply).

| Field | Limit |
|---|---|
| `identity.percentage_ownership` | 5 chars (e.g. `11.25`) |
| `contact.email_address` | 100 chars stored |
| `contact.mailing_postal_code` | 12 chars |
| `referral_source` | 128 chars — lowercase letters/numbers/underscores only |
| `disclosures.affiliated_firm` | 100 chars |

</details>

## 7. Funding

| Method | API | Typical use |
|---|---|---|
| ACH | `/v1/accounts/{id}/ach_relationships` | US bank-linked deposits/withdrawals via Plaid |
| Wire | Transfers API | Domestic/international bank wire in or out |
| Journals | `createjournal` | Internal cash/security moves, firm ↔ user, cash pooling, rewards |
| Instant funding | `/v1/instant_funding` | Immediate buying power ahead of real settlement |

Sandbox: transfer requests credit/debit the account immediately — no real banking rails.
Production wires carry a fee (since June 2022), payable by the end user
(`fee_payment_method: user`) or the firm on a monthly invoice (`invoice`).

### ACH (Plaid)
Flow: Plaid Link public token → exchange for access token → generate an Alpaca-specific
processor token → register with Alpaca.

```
# 1. Exchange public token (Plaid)
POST https://sandbox.plaid.com/item/public_token/exchange

# 2. Create Alpaca processor token (Plaid)
POST https://sandbox.plaid.com/processor/token/create
  processor: "alpaca"

# 3. Register the relationship (Alpaca)
POST /v1/accounts/{account_id}/ach_relationships
  processor_token: "<token from step 2>"
  -> status: "QUEUED" initially
```

Response: `id`, `account_id`, `status`, `account_owner_name`, `nickname`. Once `ACTIVE`, move
money with the Transfers API's `createtransferforaccount`. Sandbox timing: relationship goes
`QUEUED → APPROVED` in ~1 minute (simulated); a transfer shows up as an activity after a
simulated 10–30 minute delay; unapproved ACH requests expire after ~7 days.

### Journals (JNLC / JNLS)
Moves cash or securities between the **firm account and a user account only** — never
customer-to-customer directly.

| Type | Moves | Direction | Typical use |
|---|---|---|---|
| `JNLC` | Cash | Firm ↔ user (bidirectional) | Cash pooling, instant sandbox funding |
| `JNLS` | Securities | Firm → user only | Signup/referral reward shares |

- **Status flow (v1 / JNLS):** `queued → sent_to_clearing → executed`, or
  `rejected / refused / canceled / correct / deleted`.
- **Status flow (JNLC v2, single-journal):** `queued → sent_to_clearing` (→ `pending` if a
  limit is exceeded, awaiting manual approval) `→ executed → activity_created` (informational
  only — don't wait on it to use the updated buying power).
- v1 → v2 migration needs no code changes unless you branch specifically on
  `queued`/`sent_to_clearing` — just also handle `activity_created`.

### Instant funding
Gives immediate buying power ahead of real settlement — Alpaca or the partner fronts funds,
reconciled later.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `v1/instant_funding` | Create a transfer — needs `account_no`, `source_account_no`, `amount` |
| GET | `v1/instant_funding/:id` | Get transfer status |
| GET | `v1/instant_funding` | List transfers |
| GET | `v1/instant_funding/limits` | Correspondent-level limits |
| POST | `v1/instant_funding/settlements` | Trigger a settlement batch (requires Travel Rule fields) |
| DELETE | `v1/instant_funding/:id` | Reverse a transfer |

**Status:** `PENDING → EXECUTED` (applied to account) or `CANCELED` (settlement/reconciliation
failure).

**Limits worth knowing before a demo:** default sandbox account limit **$1,000** per customer;
default sandbox correspondent limit **$100,000**. Batching window 8 PM ET to 8 PM ET next day;
settlement deadline T+1 by 1 PM ET; unreconciled transfers auto-cancel at 8 PM ET on T+1. Max
50,000 transfers per settlement call.

**Travel Rule:** unlike FinCEN's standard $3,000+ threshold, Alpaca requires
transmitter/originator info on **all** incoming deposits regardless of amount
(`originator_full_name`, `originator_street_address`, `originator_city`,
`originator_postal_code`, `originator_country`, `originator_bank_account_number`,
`originator_bank_name`). Records retained 5 years. No read endpoint for this data — write-only
at settlement time.

## 8. Orders & fractional shares

```
POST /v1/trading/accounts/{account_id}/orders
{
  "symbol": "AAPL",
  "qty": 0.42,
  "side": "buy",
  "type": "market",
  "time_in_force": "day"
}
```

- **Fractional shares:** use `notional` (dollar amount) or `qty` (up to 9 decimals) — mutually
  exclusive. Fractional orders are day-orders only. Asset objects expose a `fractionable`
  flag; account-level toggle `fractional_trading` defaults `true`.
- **Commissions** — set via `commission` + `commission_type`: `notional` (flat $, default),
  `qty` (per-share/contract, prorated across partial fills), or `bps` (basis points). A
  sell-order commission never exceeds the transaction's principal.
- **Omnibus subtagging:** a `subtag` value per order for trade surveillance — omitting a
  required subtag "may result in order flow rejection."
- **Timeouts:** if an order submission times out, don't blindly resend or cancel — check actual
  execution status via the dashboard first; it may have gone through despite the client-side
  timeout.

## 9. Real-time events (SSE)

Server-Sent Events: a single persistent HTTP connection, replayable by timestamp or ID, lower
overhead than a WebSocket.

- **Timestamps:** RFC3339 with offset (`2006-01-02T15:04:05+07:00`) — URL-encode `+` as `%2B`
  in query strings.
- **Pagination:** prefer `since_ulid`/`until_ulid`; legacy `since_id`/`until_id` are being
  deprecated and partner-gated.
- **Protocol comment lines** (prefixed `:`): `: you are reading too slowly, dropped N messages`
  means you fell behind and must reconnect + replay; `:heartbeat` means the connection is
  alive.
- **Ordering:** guaranteed per-account chronological order; **no** cross-account ordering
  guarantee.
- Handle duplicate messages idempotently on reconnect; back up your cursor by a few minutes
  rather than the exact last-seen ID on resume (same-millisecond ULIDs aren't strictly
  ordered).

### Activity SSE (current, recommended)
The unified, current stream for trades, corporate actions, fees, journals, and transfers —
replaces the legacy NTA events, legacy fill-type Trade Events, and the REST Account Activities
API.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/v2beta1/events/activities` | Live SSE stream (beta) |
| GET | `/v2alpha1/accounts/{id}/events/activities/{event_id}` | Fetch one activity by its ULID |

Common fields on every event: `account_id`, `event_id` (ULID — sortable, use as replay
cursor), `ref_id` (stable dedup key), `activity_type` / `activity_subtype`, `at`,
`executed_at`, `settle_date`, `status`, `qty`, `price`, `net_amount`, `currency`,
`previous_id` (on corrections), `details` (type-specific).

<details>
<summary>Full activity type catalog</summary>

- **Trades:** `TRD` — `details.execution_type` is `fill`, `trade_correct`, or `trade_bust`.
- **Corporate actions (equity):** `DIV` (`CDIV`, `SDIV`, `SPD`, `ROC`), `SPLIT` (`FSPLIT`,
  `RSPLIT`, `USPLIT`), `SPIN`, `MA` (`CMA`, `SMA`, `SCMA`), `NC`, `REORG`, `VOF`, `FIMAT`,
  `DIVNRA`.
- **Options corporate actions:** `OPCA` with matching subtypes.
- **Options (non-CA):** `OPASN`, `OPEXC`, `OPEXP`, `OPTRD`, `OPCSH`.
- **Transfers & journals:** `ACATC`, `ACATS`, `FOPT`, `JNLC`, `JNLS`, `CSW`, `CSD`, `MEM`.
- **Fees:** `FEE` — `REG`, `TAF`, `ORF`, `OCC`, `NRV`, `NRC`, `LCT`, `COM`, `CAT`, `ADR`,
  `OCOM`, `BSWP`.
- **Interest:** `INT` — `MGN`, `CDT`, `SWP`, `QII`, `FPSL`, `FI`.
- **Withholding:** `WH` — `SWH`, `FWH`, `SLWH`.

**Coverage gap:** non-fill order lifecycle events (`accepted`, `canceled`, `new`) do **not**
appear on Activity SSE — only fills/corrections/busts do. For full order lifecycle tracking,
keep consuming the legacy `/v2/events/trades` stream alongside Activity SSE.

**Recommended integration flow:**
1. Connect to `/v2beta1/events/activities` with Broker API credentials.
2. Route by `activity_type`/`activity_subtype` to type-specific handlers.
3. Apply to local state (positions, cash, transaction history).
4. Persist `event_id` as your resume cursor after successful processing.
5. On reconnect, resume from the last cursor; dedupe on `ref_id`.
6. When `previous_id` is present, reverse the activity it references.

Alpaca's framing: partners are expected to maintain a backend that mirrors account state —
positions, cash, transaction history — applying each event idempotently, keyed on `ref_id`.

</details>

### KYC status events
A specialized payload variant of the Account Status Events stream
(`/v1/events/account/status`), fired when a status change relates to KYC review. Carries a
`kyc_results` object only when relevant.

| Bucket | Meaning |
|---|---|
| `accept` | No action needed unless Alpaca separately requests it |
| `indeterminate` | May require additional review/documentation |
| `reject` | Final; some reasons are non-remediable |

Reasons that request specific follow-up: `PEP` → job/occupation, `FAMILY_MEMBER_PEP` → family
member's name, `CONTROL_PERSON` → company details, `AFFILIATED` → firm details,
`VISA_TYPE_OTHER` → visa info, `W8BEN_CORRECTION` → updated form.

## 10. Statements & documents

Generated as PDF, retrieved via the Documents API (`downloaddocfromaccount`). Delivery is
URL-based — you're not required to have Alpaca deliver directly; you can own the full customer
experience. Fully-disclosed broker-dealers can brand the standard template with their logo,
name, and address.

- Trade confirmations available next day after a 02:15–02:30 AM EST batch job.
- Monthly statements available after the first weekend of the following month.

## 11. Endpoint index

| Purpose | Method | Endpoint |
|---|---|---|
| List assets | GET | `/v1/assets` |
| List accounts | GET | `/v1/accounts` |
| Create account | POST | `/v1/accounts` |
| Submit CIP | POST | `/v1/accounts/{id}/cip` |
| Account activities | GET | `/v1/accounts/activities/{type}` |
| Create ACH relationship | POST | `/v1/accounts/{id}/ach_relationships` |
| Create transfer | POST | `/v1/accounts/{id}/transfers` |
| Create journal | POST | `createjournal` (see reference) |
| Create instant funding transfer | POST | `/v1/instant_funding` |
| Trigger settlement | POST | `/v1/instant_funding/settlements` |
| Create order | POST | `/v1/trading/accounts/{id}/orders` |
| Account config | PATCH | `/v1/trading-accounts/{id}/account-configurations` |
| Activity stream | GET | `/v2beta1/events/activities` |
| Account status stream | GET | `/v1/events/account/status` |
| Journal status stream | GET | `/v1/events/journal/updates` |
| Get documents | GET | `/reference/getdocsforaccount` |

## 12. Links

- Full doc index (append `.md` to any docs URL for the markdown source):
  https://docs.alpaca.markets/us/llms.txt
- Forkable Postman workspace:
  https://www.postman.com/alpacamarkets/workspace/alpaca-public-workspace/overview
- Broker Dashboard → API/Devs → Live Testing tool for browser-based requests without writing
  code first
- Plaid × Alpaca partnership setup: https://plaid.com/docs/auth/partnerships/alpaca/
- Instant funding walkthrough:
  https://alpaca.markets/learn/getting-started-with-instant-funding-for-broker-api/
