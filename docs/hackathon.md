# Alpaca AI Trading Agents Hackathon

Sources:
- https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon (event page)
- https://lablab.ai/hackathon-rules (lablab.ai Hackathon Rule Book — platform-wide)
- https://lablab.ai/getting-started-guide (lablab.ai Getting Started Guide — platform-wide)
- https://lablab.ai/ai-articles/hackathon-guidelines (step-by-step registration/submission tutorial — platform-wide)

Scraped: 2026-08-28

## Overview

- **Dates:** 28 August – 4 September 2026 (7 days)
- **Format:** Online, fully remote — join and build from anywhere
- **Prize pool:** $6,300 total
- **Cost:** Free to enter, no purchase or funding required
- **Tagline:** "Code the next generation of algorithmic trading"
- **Lead partner:** Alpaca (programmable brokerage)
- **Platform:** lablab.ai (registration/submissions) + lablab.ai Discord server (community/support)

Build AI trading agents on Alpaca — autonomous agents and trading apps using Alpaca's Trading API, MCP server, and CLI. Alpaca provides the regulated brokerage infrastructure (US stocks, options, ETFs, crypto); participants build the intelligence layer. All development and testing happens in Alpaca's **paper trading environment** (simulated funds, real market data — no real capital needed).

## The Challenge

**Main Challenge: Options Alpha Agents**

Build an autonomous AI trading agent designed to generate P&L using Alpaca's trading platform. Develop a clear, testable trading strategy and demonstrate how your agent identifies opportunities, makes trading decisions, manages positions, and performs over the course of the competition. Approaches may explore options, trading agents, portfolio income, or other strategies supported by Alpaca.

### Core requirements
1. **Autonomous agents** — must build autonomous AI trading agents using Alpaca's Trading API.
2. **MCP or CLI** — must utilize either Alpaca's MCP server or its CLI tools.
3. **Options trading** — all strategies must incorporate options trading.

### Account requirements
- **During development:** use any paper trading account to prototype/experiment.
- **For final submission (judging):** you must create a **brand-new Alpaca paper trading account** dedicated to this hackathon. Projects run on an existing/reused account are **not eligible** for judging.
- **Starting balance:** the competition account must be set to **$100,000**.
- **One-page write-up required**, covering your AI logic, risk gates, and Alpaca infrastructure implementation.

### Extra challenge — Social engagement (optional, for a separate prize track)
- Build in public: share progress on **X** and **LinkedIn** while building — process, reasoning, and setbacks included.
- Tag both **lablab.ai** (X: @lablabai, LinkedIn: lablab.ai) and **Alpaca** (X: @AlpacaHQ, LinkedIn: Alpaca) in posts.
- You may submit **up to 5 social media post links** with your final project submission.

## Timeline / Event Schedule

All times as listed on the event page (mixed timezones as published):

| Event | Time |
|---|---|
| Hackathon Kick-off | Fri Aug 28 2026, 17:00 (GMT+2, CEST) |
| lablab.ai Opening words | Fri Aug 28 2026, 19:05 (GMT+4, Gulf Standard Time) |
| Alpaca Opening words | Fri Aug 28 2026, 19:10 (GMT+4, Gulf Standard Time) |
| Introduction to the Challenge | Fri Aug 28 2026, 19:15 (GMT+4, Gulf Standard Time) |
| Hackathon Guide | Fri Aug 28 2026, 19:25 (GMT+4, Gulf Standard Time) |
| Discord Q&A session | Fri Aug 28 2026, 18:00 (GMT+2, CEST) |
| **End of Submissions!** | **Fri Sep 04 2026, 17:00 (GMT+2, CEST)** — equivalent to **15:00 UTC** |

(Note: the JSON-LD event metadata gives the overall event window as `2026-08-28T15:00:00Z` → `2026-09-04T15:00:00Z`.)

Speakers/Mentors/Judges: not yet announced on the page at time of scraping.

## Partners

**Lead partner — Alpaca**
Alpaca is a brokerage platform providing access to financial markets through developer-friendly APIs. Alpaca's Broker API is full-stack brokerage infrastructure empowering fintechs/institutions to build investing apps. Its Trading API lets programmatic traders, retail investors, hedge funds, and prop firms automate strategies across stocks, options, and crypto.
- Securities trading offered by Alpaca Securities LLC (dba "Alpaca Clearing"). Crypto trading offered by Alpaca Crypto LLC.
- **Trading API** — the programmable brokerage interface for orders on US stocks, options, ETFs, and crypto.
- **MCP server** — lets AI assistants (Claude, Cursor, VS Code, ChatGPT) interact with Alpaca's APIs via structured tools in the paper-trading environment.
- **Alpaca CLI** — same trading functions from a terminal, structured JSON output; built for long-running agent sessions, cron jobs, CI.
- **Paper trading environment** — simulated funds with real market data, free, no card required.
- Site: alpaca.markets | Docs: docs.alpaca.markets

**Technology partner — Featherless AI**
Serverless AI inference for open-source models, for integrating specialized models into agents/workflows/real-time apps.
- Build with it: add open-source model inference to agent workflows; build research/automation/extraction agents; power agent reasoning & generation.
- **Access:** **$25 in credits per participant**, **first-come, first-served**, pay-per-request, active until credits run out.
- **Setup guide:** a screen-recorded quick-start/demo video — https://drive.google.com/file/d/1zslhjy1F_0My0W1Hr4ctMz25eQ7oZcDb/view ("Featherless AI - Quick Start and Live Demo.mov", publicly viewable, no access request needed).
- Links: https://featherless.ai/ | https://lablab.ai/tech/featherless

> To be eligible for **partner prizes**, the relevant partner technology must be integrated into a project submitted under the hackathon challenge.

**Notes on what's *not* documented on the page:**
- The page's raw HTML contains a commented-out (i.e. currently **hidden/disabled**) "AVAILABILITY: Up to 500 participants" line — so a 500-participant cap may have existed at some point but is **not currently shown live** on the page. Treat it as unconfirmed/possibly outdated rather than an active limit.
- There's no visible promo code, redemption form, or sign-up link for the $25 Featherless credit anywhere in the page source — only the "Setup Guide" video linked above and the two footer links. The actual redemption mechanism (e.g. a code shared in Discord, or something inside the video) isn't captured in the page's text/HTML, so it's likely announced separately (Discord announcement, the setup-guide video itself, or revealed after signing up on featherless.ai) rather than published as text on the hackathon page.

## Resources ("Build with Alpaca")

**01 — Start here**
- Getting Started: https://docs.alpaca.markets/us/docs/getting-started

**02 — Developer tools**
- Alpaca Skills (AI-powered dev resources): https://github.com/alpacahq/alpaca-skills
- Trading API: https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
- Market Data API: https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data
- Alpaca JS SDK: https://github.com/alpacahq/alpaca-trade-api-js
- Alpaca Python SDK: https://github.com/alpacahq/alpaca-py
- Alpaca CLI: https://github.com/alpacahq/cli

**03 — AI & agent development**
- Trading MCP Server docs: https://docs.alpaca.markets/us/docs/alpaca-mcp-server
- Multi-Agent AI Trading System (guide): https://alpaca.markets/learn/building-a-multi-agent-ai-trading-system-on-alpaca

**04 — Documentation**
- Trading CLI Documentation: https://docs.alpaca.markets/us/docs/alpacas-cli
- SDKs & OpenAPI Specs: https://docs.alpaca.markets/us/docs/sdks-and-tools

**Other links**
- Alpaca Slack (community): https://alpaca.markets/slack
- Alpaca disclosures: https://alpaca.markets/disclosures

**Broker API (not used by this project — see note)**
- [broker_api_reference.md](broker_api_reference.md) has swept notes on Alpaca's Broker API
  (sandbox environments, KYC/account-creation rules, funding, events). Kept for reference only:
  this hackathon runs on the plain **Trading API** + a personal paper account (see Account
  requirements above), not the Broker API.

## Prizes

**Total prize pool: $6,300**

### Main prizes
| Place | Prize |
|---|---|
| 🥇 1st | $2,500 + $300 in Featherless credits |
| 🥈 2nd | $1,500 |
| 🥉 3rd | $1,000 |

### Social Engagement prize — 2 winning teams
Each winning team receives:
- **$500 USD** for the team
- **1-month Algo Trader Plus subscription** (from Alpaca) for **every member** of the winning team, individually

### Prize terms
- **Sponsor:** AlpacaDB, Inc. pays the $6,000 pool directly in USD.
- **Eligibility:** 18+. Not available to Alpaca employees, contractors, immediate family/household members, or participants from sanctioned countries. Void where prohibited. No purchase or Alpaca account required.
- **Individual payee:** prizes are paid to individuals, not teams/companies. Teams must designate one member to receive the full amount (or confirm a split with Finance in advance).
- **Taxes & documents:** W-9 (US) or W-8BEN (non-US), government photo ID, and bank details required before payment.
- **Payment:** Alpaca pays within 90 days of event end once documents clear (incl. international sanctions screening).
- US winners earning >$600 receive a 1099-MISC. Non-US payments generally subject to 30% US withholding unless a valid tax-treaty claim applies on the W-8BEN. Gross prizes may be reduced by withholding/wire fees.
- Winners must complete required documentation within 90 days of notification or the prize may be forfeited.
- This is a **skill contest**; judging is final. Submissions must be **original and MIT-compliant**.
- Alpaca may use winner name/likeness/project for publicity without extra compensation, and may modify or cancel prizes if the event changes/is cancelled.

## How to Participate

1. Register on **lablab.ai** and join the **lablab.ai Discord server** (both required).
2. Click **Enroll** on the event page, and read the Hackathon Guidelines, Getting Started Guide, and the lablab.ai Hackathon Rule Book.
3. Open to all skill levels — no prior AI/coding experience required.
4. Before kickoff: browse the **AI Tech** and **tutorials** pages on lablab.ai for a head start.

### Teams
- Teams of **1–6 people**.
- No team yet? Connect with other participants on the lablab.ai dashboard or Discord server.

## Registration — Detailed Step-by-Step (lablab.ai platform-wide process)

This is the general lablab.ai flow (from the Getting Started Guide + Hackathon Guidelines tutorial) that applies to every hackathon on the platform, including this one:

1. **Complete your lablab.ai profile** — https://lablab.ai/profile
2. **Register for the event** via the event page (this hackathon: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon). Click the green **"Enrol Now"** button, usually at the bottom of the page.
   - If you don't already have a lablab.ai account, you'll be prompted to sign up and create a participant profile first.
3. **Connect your Discord account** to your lablab.ai profile — required before you can create/join a team. Join the **lablab.ai Discord server** as well.
4. **Create or join a team**:
   - *Create:* On the hackathon page (once Discord is connected), click **"Create or Join a team"** → **"Create a team"** → fill in team details. Invite teammates via an invitation URL ("Add Teammate" button), or use "Invite" on the participant list to notify prospective teammates by email/Discord.
   - *Join:* Either (a) browse existing teams on the hackathon page for a **"looking for members"** badge and message them via their team dashboard, or (b) post in the `#looking-for-team` channel on Discord (or the "Active Hackathon" channel / "Looking for a Team" thread).
   - Team size: **max 6 participants**. Solo participation is also fine.
   - Tip: mix of skills (dev + design/business) tends to produce stronger projects.
5. **Build your project** during the event window, using the "Calling for Help" feature on your team's dashboard or the mentor channel on Discord (tag `@Mentor`) for support. Post-kickoff you'll get an email with scheduling links for optional 1:1 mentor calls.
6. **Submit your project** before the deadline via the **"Submit project"** button on your team's dashboard (see submission field details below).

### Discord server structure
- Mentor help: post in the designated mentor-help channel and tag `@Mentor`.
- General questions/clarifications: use the general-inquiries channel.
- Finding teammates: "Active Hackathon" channel / "Looking for a Team" thread, or `#looking-for-team`.
- Team coordination: dedicated channels under **"TEAM VOICE CHANNELS"** (includes integrated text chat).
- Kick-off and key sessions are live-streamed on lablab.ai's **Twitch channel** (https://www.twitch.tv/lablabai); recordings are later posted to Discord.

## What to Submit

**Basic information**
- **Project (submission) title** — concise, max **50 characters**.
- **Short description / summary** — max **255 characters**.
- **Long description** — comprehensive explanation of your idea, minimum **100 words**.
- **Main Tracks** — select the primary categories that apply (as listed on the hackathon page).
- **Technology & category tags** — list all technologies used; the reference list is at https://lablab.ai/tech. Proper tagging matters for judging/categorization.

**Cover image and presentation**
- **Cover image** — PNG or JPG, **16:9 aspect ratio**.
- **Video presentation** — MP4, mandatory; keep it **under 300MB and within ~5 minutes**.
- **Slide presentation** — PDF, mandatory.

**App hosting and repository**
- **Public GitHub repository** — mandatory. If you have multiple repos, list them in your `README.md`.
- **Demo application platform** — e.g. Streamlit, Replit, or Vercel.
- **Demo application URL** — required for interactive/judged evaluation.
- **Alpaca paper trading account ID** (required for judging — identifies your trading activity so judges can evaluate P&L)
- **Additional information** (optional) — anything else judges should know, e.g. how the solution could scale.

**Social engagement (optional)**
- Up to 5 links to posts on X or LinkedIn, tagging both lablab.ai and Alpaca

## Judging Criteria

1. **P&L Performance** — trading performance of the submitted agent in the Alpaca paper trading environment; judges weigh the project's P&L and how effectively the strategy performs.
2. **Technology Implementation** — how effectively the project uses Alpaca's Trading API, MCP server, CLI, and other required tech to build an autonomous trading agent.
3. **Creativity & Originality** — originality of concept, trading strategy, agent behavior, and overall approach.
4. **Presentation & Execution** — clarity/effectiveness of communication, demo of the agent in action, and explanation of strategy/results.
5. **Social engagement** (separate track) — quality of content and engagement generated (likes, comments, shares), plus creativity/usefulness of posts.

> The four criteria above are the **event-specific** judging rubric for this hackathon. lablab.ai's platform-wide Hackathon Rule Book also defines a generic 1–5 scoring rubric (Presentation, Business Value, Application of Technology, Originality) used as a baseline across all lablab.ai hackathons — see **Generic Platform Judging Rubric** below. Where the two differ, the event-specific criteria above take precedence for this hackathon.

## Rules & Conduct (lablab.ai Hackathon Rule Book — platform-wide)

Source: https://lablab.ai/hackathon-rules — "Please read these rules carefully. Non-compliance may result in lower scores or disqualification."

### Submission guidelines
- Failure to adhere to submission guidelines (see field limits above) may result in a **lower score or exclusion** from the hackathon.

### Generic Platform Judging Rubric
Each criterion below is scored 1 (poor) to 5 (excellent):

**1. Presentation** (PDF + video)
| Score | Description |
|---|---|
| 1 – Poor | No description of problem or gaps to fill. |
| 2 – Limited | Problem & solution not effectively communicated / hard to understand. Video < 3 min. |
| 3 – Adequate | Effectively communicates problem, solution, value proposition in < 5 min. No market analysis/revenue, no future plans. |
| 4 – Strong | Same as above, but also explains market analysis, marketing revenue, and future goals/plans. |
| 5 – Excellent | Exceptional in every aspect; flawlessly communicates problem/solution/value; shows strengths & uniqueness via competitive analysis. |

**2. Business value**
| Score | Description |
|---|---|
| 1 – Limited | Little/no potential for commercial viability; doesn't address a real problem/need. |
| 2 – Some | Some potential; uncertain feasibility/scalability/revenue; niche market. |
| 3 – Moderate | Reasonable value; addresses a market need with revenue potential; needs more validation. |
| 4 – High | Clear market potential; could attract a large customer base; strong feasibility/scalability. |
| 5 – Exceptional | Potential to disrupt the industry/create a new market; clear sustainable revenue path. |

**3. Application of technology**
| Score | Description |
|---|---|
| 1 – Poor | No demo video, no working demo link, no GitHub. |
| 2 – Limited | Demo video incomplete; framework unclear; demo link/GitHub missing or not working. |
| 3 – Adequate | Demo video shows all features; demo link works (minor rough edges); GitHub partial. |
| 4 – Strong | Demo video complete; demo link well-executed; GitHub available & well thought out. |
| 5 – Excellent | Exceptional use of AI tech across demo/video/GitHub; flawless technical implementation. |

**4. Originality**
| Score | Description |
|---|---|
| 1 – Not Original | Exact copy of an existing solution. |
| 2 – Limited | Common idea, lacks differentiation. |
| 3 – Moderate | Some unique idea/approach vs. existing non-AI solutions (e.g. saves time/cost). |
| 4 – Highly Original | Unique/innovative approach, unconventional methods, novel combinations. |
| 5 – Exceptional | Transformative idea; completely new perspective; potential to disrupt the industry. |

*(These are the platform's baseline rubric categories used across lablab.ai events, by Walaa Nasr. The Alpaca hackathon's own judging criteria — P&L Performance, Technology Implementation, Creativity & Originality, Presentation & Execution, Social Engagement — are listed above and are what this event is actually scored on.)*

### Technical issues & manual submission
- If the submission system fails, **manual submission is available for up to 6 hours post-hackathon**, but only for participants with a valid reason **and prior approval from organizers or mentors**.

### Ethical conduct
- Unethical behavior — plagiarism, gaming the voting system, cheating, tampering with systems, unauthorized automation, fraud, or any other conduct judged as undermining fairness — leads to **immediate disqualification** and possible removal from the event.

### Mentor & organizer participation
- Organizers may participate but are **not eligible for prizes**.
- If a mentor or organizer participates as a competitor, they **cannot also serve as a judge**.

### Judges' Code of Conduct
- Maintain **confidentiality** of all submissions.
- **Abstain from judging** in case of a conflict of interest.
- **Declare any affiliations** that might compromise impartiality.
- Do **not copy, retain, or share** any entry materials.

### Mini-hackathon specific rules
- All general rules apply; this doesn't appear to be a "mini hackathon," but note that mini-hackathons get **limited mentor support** since they're treated as an expert challenge.

### Related guides (lablab.ai)
- [AI Hackathons: The Complete Guide](https://lablab.ai/guide/ai-hackathons) — what to expect at every stage
- [How to Win an AI Hackathon](https://lablab.ai/guide/how-to-win-an-ai-hackathon) — deep dive on judging criteria & demo strategy
- [Submission Guidelines / full checklist](https://lablab.ai/delivering-your-hackathon-solution)
- [Best AI APIs for Hackathons](https://lablab.ai/guide/best-ai-apis-for-hackathons)

## Community & Social Channels

**Alpaca:** X · LinkedIn · GitHub · Slack · Forum · Website (alpaca.markets)
**lablab.ai:** X (@lablabai) · LinkedIn · Instagram · YouTube · Twitch · Discord (discord.gg/lablabai) · Website
**NativelyAI:** Website · LinkedIn · Discord

## Disclosures (important legal notes)

- Content on the hackathon page is general informational only — **not investment advice**.
- lablab.ai and Alpaca are unaffiliated, each responsible for their own liabilities.
- Hackathon projects use Alpaca's **paper-trading environment** — a simulation, no real securities transactions or funds. Paper-trading results are hypothetical and don't guarantee future real results.
- Securities brokerage: Alpaca Securities LLC (dba "Alpaca Clearing"), member FINRA/SIPC, subsidiary of AlpacaDB, Inc.
- Crypto services: Alpaca Crypto LLC, a FinCEN-registered MSB (NMLS #2160858); not a member of SIPC/FINRA; crypto not protected by FDIC/SIPC.
- **Options trading is not suitable for all investors** due to inherent high risk (can result in significant losses); complex strategies carry additional risk. Read "Characteristics and Risks of Standardized Options" before trading options.
- All investments involve risk, including possible loss of principal.

## Quick-reference checklist for our team

**Registration**
- [ ] Complete lablab.ai profile (https://lablab.ai/profile)
- [ ] Enroll on the event page (green "Enrol Now" button)
- [ ] Join lablab.ai Discord server **and** connect Discord to lablab.ai profile
- [ ] Create/confirm our team on the event page (max 6 members)
- [ ] Read: Hackathon Guidelines, Getting Started Guide, lablab.ai Hackathon Rule Book

**Build**
- [ ] Set up Alpaca account, explore Trading API / MCP server / CLI with a dev paper account
- [ ] Design an autonomous trading agent strategy that **must include options trading**
- [ ] Before final submission: create a **fresh** Alpaca paper account, fund with **$100,000** starting balance
- [ ] Build + test agent (MCP or CLI required) against paper trading
- [ ] Write the required one-page write-up (AI logic, risk gates, Alpaca infra)
- [ ] Follow ethical conduct rules (no plagiarism, no gaming votes/systems) — instant DQ risk

**Submission assets (mind the limits!)**
- [ ] Title (≤50 chars), short description (≤255 chars), long description (≥100 words)
- [ ] Main track(s) + technology tags (from lablab.ai/tech)
- [ ] Cover image (16:9, PNG/JPG)
- [ ] Video presentation (MP4, <300MB, ~5 min max)
- [ ] Slide presentation (PDF)
- [ ] Public GitHub repo (list multiple repos in README if needed)
- [ ] Demo platform + live demo URL
- [ ] Alpaca paper trading account ID (the fresh one used for judging)
- [ ] (Optional) Up to 5 social post links (X/LinkedIn), tagging @lablabai and @AlpacaHQ

**Deadline**
- [ ] Submit before **4 September 2026, 15:00 UTC** (manual submission only possible up to 6h late, with prior mentor/organizer approval)
