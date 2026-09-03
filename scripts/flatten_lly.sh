#!/usr/bin/env bash
# Force the last open position flat. Run at 13:30 UTC Fri 4 Sep, at the open,
# BEFORE the agent's 14:15 scan gets there.
#
# WHY THIS EXISTS
#   Trade 8 (LLY 1160/1165 bear put vertical, 4 spreads) survived the 3 Sep
#   unwind: 5 walk segments, 250 replaces, last order EXPIRED at 19:59:53 when
#   RTH ended. The contracts expire 2026-09-04.
#
# WHY IT MATTERS, given scoring is already locked to EOD Thu 3 Sep equity
#   Spot closed at 1160.62 -- between the strikes. At expiry:
#     S >= 1165          both worthless, position value 0
#     S <= 1160          both exercised, net +$2,000
#     1160 < S < 1165    long 1165P auto-exercised by OCC exception, short
#                        1160P expires worthless  ->  SHORT 400 SHARES LLY,
#                        ~$464,000 notional, on a $94k account, over a weekend.
#   That last band is where spot actually is. Closing costs ~$50 more than the
#   mark the account already carries and removes the whole tail.
#
# PROTOCOL: short leg first, always. Buying back the short leaves a long put
# (risk bounded by premium). Selling the long first would leave a naked short
# put. Never reverse these two steps.

set -euo pipefail

SHORT_LEG="LLY260904P01160000"   # currently -4
LONG_LEG="LLY260904P01165000"    # currently +4

banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

banner "0. Pre-flight"
alpaca clock
alpaca position list
alpaca order list --status open

read -r -p $'\nMarket open and both legs still held? Proceed to flatten? [yes/NO] ' ok
[ "$ok" = "yes" ] || { echo "aborted"; exit 1; }

banner "1. Cancel anything the agent left resting"
alpaca order cancel-all || true

banner "2. Buy back the SHORT leg first ($SHORT_LEG)"
alpaca position close --symbol-or-asset-id "$SHORT_LEG"

banner "2b. Confirm the short leg is gone BEFORE touching the long"
sleep 5
alpaca position list
if alpaca position list | grep -q "$SHORT_LEG"; then
  echo "!! SHORT LEG STILL HELD -- STOP. Do not sell the long leg; that would"
  echo "!! leave a naked short put. Work the short leg by hand until flat."
  exit 2
fi

banner "3. Sell the LONG leg ($LONG_LEG)"
alpaca position close --symbol-or-asset-id "$LONG_LEG"

banner "4. Verify flat"
sleep 5
alpaca position list
alpaca account get

cat <<'NOTE'

If either leg refuses to fill (the book was inverted all day 3 Sep -- 1165P bid
2.72 vs 1160P bid 5.58), the fallback ladder is:

  a. Retry with a marketable limit through the ask rather than a market close:
       alpaca order submit --symbol LLY260904P01160000 --qty 4 --side buy \
         --type limit --limit-price 11.00 --time-in-force day \
         --position-intent buy_to_close

  b. Last resort, before 15:30 ET: decline exercise on the long leg so it cannot
     turn into a short stock position overnight. This FORFEITS its intrinsic
     value -- only do it if the short leg is already closed:
       alpaca option do-not-exercise --symbol-or-contract-id LLY260904P01165000

  c. If the short leg cannot be closed and spot is below 1160 near the bell,
     expect assignment: +400 shares LLY. Flatten the equity immediately Monday:
       alpaca position close --symbol-or-asset-id LLY

NOTE
