DELIVERABLE 1: AI Avatar, Voice & Tech Stack (1:30 Compression)
1. Voice Synthesis Configuration (ElevenLabs)
Model: eleven_turbo_v2_5 (Lower latency, locked high-cadence stability)
Voice Profile: "Marcus" or "Adam" (Deep, authoritative baritone, deadpan institutional delivery)
Voice Parameters:
Stability: 0.75 (Increased slightly to prevent pitch modulation at 140 WPM)
Similarity Boost: 0.85
Style Exaggeration: 0.00 (Strictly deadpan, rapid-fire executive delivery)
Speaker Boost: Enabled
Pacing & Cadence: 140 WPM locked. Total word count: 210 spoken words 
→
→
 Exact spoken runtime: 1 minute 30 seconds (90s).
2. Avatar Generation Pipeline
Engine: HeyGen Studio / API (Dark Crewneck, transparent alpha channel or #00FF00 chroma background).
Composition: 1080x1920 portrait asset scaled down to 480x270 PiP inset.
Failover Trigger: If HeyGen queue time > 10 minutes at T-15h, immediately cut to Terminal Reactive HUD (animated SVG audio visualizer + fullscreen terminal). At 90 seconds, the terminal-only cut is even punchier and saves 40 minutes of render/sync time.
3. Layout Composition
0:00–0:18: Fullscreen Avatar flanked by kinetic typography ($95,133.99, -4.87%).
0:18–1:15: Terminal & Dashboard split-screen (75% screen area) with Avatar PiP in bottom-right corner.
1:15–1:30: Dark slate HUD (#09090B) with architecture flow diagram and verified credentials.
DELIVERABLE 2: Real Capture & B-Roll Assembly Pipeline (1:30 Run)
Execute these exact, verified commands in the workspace to gather the 5 primary visual evidence clips:
code
Bash
# 1. LIVE BROKER ACCOUNT STATUS (Hook evidence)
alpaca account get
alpaca position list

# 2. ADVERSARIAL DETERMINISTIC VETO TEST (Verified working test)
pytest agent/tests/test_main.py::test_unanimous_approve_of_oversized_trade_rejected -v

# 3. BUG FIX VERIFICATION: Commit ac54d36 & Negative Credit Walk
pytest agent/tests/test_order_manager.py -v -k "walk or credit or replace" --tb=short

# 4. QUERY ACTUAL PRODUCTION LEDGER DISCREPANCY & LLY RUNAWAY WALK
AGENT_DB_PATH="<railway-postgres-dsn>" python -c "
import asyncio, agent.storage.db as db
async def main():
    async with db.connect(__import__('os').environ['AGENT_DB_PATH']) as c:
        rows = await (await c.execute('''
            SELECT symbol, structure, qty, submitted_limit, fill_price, walk_steps 
            FROM trades WHERE symbol='LLY' OR walk_steps > 0 ORDER BY ts_utc DESC LIMIT 5''')).fetchall()
        for r in rows: print(tuple(r))
asyncio.run(main())"
B-Roll Asset Bins (90-Second Edit)
Clip 1 (broll_equity.mp4): OBS zoom on alpaca account get 
→
→
 $95,133.99 (-4.87%).
Clip 2 (broll_adversarial_pass.mp4): Terminal macro on PASSED: test_unanimous_approve_of_oversized_trade_rejected.
Clip 3 (broll_lly_telemetry.mp4): Dashboard detail on Eli Lilly: 4 spreads (8 sides), Mid: 1.94, Fill: 6.65, Steps: 95, Slippage: -$1,884.
Clip 4 (broll_sdk_fix.mp4): Git diff of ac54d36 in alpaca_client.py (limit_price <= 0 bypass) and walk stepping: -5.01 → -4.96 → -4.91.
Clip 5 (broll_vrp_tautology.mp4): Code view of agent/backtest/llm_replay.py:222 (BACKTEST_IV_RV_MULTIPLIER).
DELIVERABLE 3: The Complete Beat-by-Beat Script & Storyboard (0:00 – 1:30)
Exact Word Count: 210 words | Delivery Cadence: 140 WPM | Total Runtime: 90 seconds (1:30).
Timestamp & Duration	Section Title	Visual / B-Roll / Telemetry Cue	Voiceover Script (Exact Words)
0:00 – 0:18<br>(18 sec / 40 words)	1. The Hook & Uncurated Reality	0:00-0:08: Center Avatar. Hard cut to terminal: alpaca account get showing equity $95,133.99 (-4.87%).<br><br>0:08-0:18: Kinetic callout: PA3UM9X4MN5X. Red stamp over synthetic curve: "HONEST IGNORANCE > FALSE CONFIDENCE".	Most trading demos lie with curve-fitted paper backtests. This is Alpaca account PA3UM9X4MN5X. Real capital started at one hundred thousand dollars. Current equity sits at ninety-five thousand, one hundred thirty-three dollars—down four point eight seven percent. Completely uncurated and auditable.
0:18 – 0:38<br>(20 sec / 48 words)	2. Quant Spine & Deterministic Veto	0:18-0:28: Screencast of agent/ticker_screener.py & debate feed (DeepSeek-V3.1 vs Kimi-K2, $4/day budget ceiling).<br><br>0:28-0:38: Terminal zoom: pytest agent/tests/test_main.py::test_unanimous_approve_of_oversized_trade_rejected 
→
→
 PASSED.	We trade volatility risk premium across a fifty-name universe. Regime selection is pure arithmetic: cross-sectional IV over RV sign-guarded above one point zero. Models debate strikes, but deterministic Python enforces all risk. When our adversarial test feeds a unanimous LLM approval for an oversized trade, code vetoes it every time.
0:38 – 1:04<br>(26 sec / 60 words)	3. Forensic Post-Mortem: Fills, Bugs, & Tautology	0:38-0:48: Friction split-card (docs/friction.md): Regulatory: 
5.21
∗
∗
v
s
S
l
i
p
p
a
g
e
:
∗
∗
5.21∗∗vsSlippage:∗∗
1,961 (376x). LLY row: 95 steps, fill 6.65.<br><br>0:48-0:58: Commit ac54d36 diff on screen: ValueError: limit_price <= 0. Show failed 0, 0, 0, 0 steps vs fixed -5.01 → -4.96.<br><br>0:58-1:04: Code zoom: llm_replay.py:222 (BACKTEST_IV_RV_MULTIPLIER).	Execution destroys paper backtests. Regulatory costs were five dollars; order slippage was nineteen hundred sixty-one dollars—three hundred seventy-six times higher. On Eli Lilly, our walker stepped ninety-five times, losing eighteen hundred eighty-four dollars on four spreads. Alpaca's SDK threw unhandled errors on negative limits, silently locking close attempts at zero steps until commit ac54d36. We also published our harness's synthetic VRP tautology.
1:04 – 1:20<br>(16 sec / 38 words)	4. Commercial Angle & Ledger Honesty	1:04-1:14: Diagram: LLM Agents 
→
→
 Deterministic Execution Firewall 
→
→
 Broker Venue.<br><br>1:14-1:20: UI discrepancy card: Booked -
465
v
s
A
c
t
u
a
l
−
465vsActual−
241 ($224 understated loss published live).	The commercial product isn't another predictive LLM; it's deterministic execution middleware. Agents cannot trade without mathematical walk caps and signed multi-leg safety spines. We even published an unbackfilled two hundred twenty-four dollar ledger divergence between our database and the broker.
1:20 – 1:30<br>(10 sec / 24 words)	5. Roadmap & Final Verdict	1:20-1:30: Dark slate card. Pinned repository link: github.com/straf10/Autonomous-Debate-Trading-Agent and Account bc8bc895-... (PA3UM9X4MN5X). Fast fade.	Next: independent IV sourcing, walk-forward validation, and combinatorial cross-validation. Inspect our entire codebase on GitHub, and verify account PA3UM9X4MN5X. Real quant systems survive the autopsy.
DELIVERABLE 4: Critical-Path 17-Hour Countdown Schedule (90s Pipeline)
code
Code
[T-17h] 22:00 UTC ── SCRIPT LOCK & DIRECT TERMINAL CAPTURES
│                   • Lock script at verbatim 210 words (1:30 duration at 140 WPM).
│                   • Run `alpaca account get` to confirm equity ($95,133.99).
│                   • Screen-record the 5 essential clips in Deliverable 2 (1080p60 OBS).
│
[T-15h] 00:00 UTC ── FAST-TRACK AUDIO SYNTHESIS & AVATAR DISPATCH
│                   • Generate ElevenLabs voice track (Marcus / Adam, 140 WPM, ~90 seconds).
│                   • Send 90s audio master to HeyGen Studio (renders 3x faster than 4m cut).
│                   • Set fallback trigger at T-14h: if queue > 10m, lock Terminal HUD visualizer.
│
[T-12h] 03:00 UTC ── TIMELINE STITCH & RAPID CUTTING
│                   • Lay down master audio. Cut terminal footage to match the 5 beats.
│                   • Visual cuts every 2–3 seconds (high pace, zero dead air).
│
[T-9h]  06:00 UTC ── TELEMETRY HUD & KINETIC CAPTION OVERLAYS
│                   • Generate punchy kinetic captions (1–2 words per slice, bold monospace).
│                   • Overlay graphics: $1,961 vs $5.21, commit `ac54d36`, and `PASSED` test stamps.
│                   • Audio mix: Master voice to -14 LUFS, hard limiter at -1.0 dBFS.
│
[T-5h]  10:00 UTC ── FINAL COMPLIANCE PASS
│                   • Verify hard runtime is between 1:28 and 1:32.
│                   • Confirm all ground truth numbers ($95,133.99, $1,961, ac54d36, LLY 95 steps,
│                     PA3UM9X4MN5X) are visible and spoken.
│
[T-2h]  13:00 UTC ── FFmpeg PRODUCTION ENCODING (< 300MB)
│                   • Execute FFmpeg encoding command below.
│
[T-1h]  14:00 UTC ── PORTAL SUBMISSION & MIRROR HOSTING
│                   • Upload MP4 to hackathon submission dashboard.
│                   • Mirror unlisted copy to YouTube & Google Drive. Lock repo README links.
│
[T-0h]  15:00 UTC ── DEADLINE / MISSION COMPLETE
Final Production FFmpeg Command (Optimized for 90 Seconds)
code
Bash
ffmpeg -y -i master_edit_90s.mov \
  -c:v libx264 -preset slow -profile:v high -level 4.2 -crf 17 \
  -maxrate 12M -bufsize 24M -pix_fmt yuv420p \
  -c:a aac -b:a 256k -ar 48000 \
  -movflags +faststart \
  autonomous_debate_trading_agent_90s.mp4
Expected file size for 90 seconds at these ultra-high-clarity bitrate settings: ~65MB–85MB (well below the 300MB limit with crisp, pixel-perfect CLI text readability).