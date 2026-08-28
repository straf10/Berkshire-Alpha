# Project instructions

See [plan.md](plan.md) for the full build plan, architecture, and timeline.

## Git commits

Never add a `Co-Authored-By` trailer (or any AI-attribution line) to commit messages in this repo.

## Token/cost efficiency

Applies both to how Claude Code should work in this repo, and as design
constraints for the agent's own LLM pipeline (`agent/tools/llm.py`,
`agent/agents/*`, per plan.md's "LLM budget & fallback").

1. **Edit, don't rewrite.** When changing an existing file, make a targeted
   edit (diff/search-replace) rather than regenerating the whole file. A
   500-line file with a 2-line fix should cost tens of tokens, not thousands.
2. **Don't parallelize the first call into unfamiliar context.** The first
   large prompt against a new file, codebase area, or conversation is an
   expensive cache write. Run that one call synchronously first so it lands
   in the provider's cache, then fan out parallel follow-up calls — they'll
   hit as cheap cache reads instead of each paying full price. Applies to
   Claude Code's own exploration and to the agent's LLM client when it fans
   out analyst calls.
3. **Keep tool/function schemas passed to an LLM compact.** No prose
   paragraphs explaining domain concepts the model already knows (e.g. what
   IV/RV is) — a short name plus a minimal JSON/YAML schema is enough. This
   applies to any tool-calling or structured-output definitions in the
   agent's Pydantic schemas and prompts.
4. **Filter context before the expensive call, not after.** When a prompt
   would otherwise include a large raw dump (news headlines, Reddit threads,
   logs), strip it to what's relevant to the specific query first — cheap
   keyword/date filtering or a small/cheap model pass — rather than handing
   the full dump to the main model.

## Alpaca CLI

Installed at `%LOCALAPPDATA%\Programs\alpaca-cli\alpaca.exe`, on PATH, authenticated
as profile `paper` against the judged account.

**Use Git Bash, never Windows PowerShell 5.1, for any `alpaca` command that passes a
JSON argument (e.g. `--legs` for `mleg` orders).** PowerShell 5.1 mangles the quoting
before it reaches the native exe — the error is `invalid character 's' looking for
beginning of object key string`, which is misleading (it's a shell quoting bug, not a
malformed JSON payload). Plain-flag commands (no JSON) work fine in either shell.

Options greeks/IV require `--feed indicative` explicitly — see plan.md's "Fills,
quotes, and data" section. The default `opra` feed returns live quotes but empty
greeks and null IV, with no error.

## Session memory

After completing any significant task (a feature, a fix, a deploy, a config
decision — not trivial edits), append a dated entry to `memory.md` summarizing
what changed, why, and anything the next session should know. `memory.md` is
gitignored — it's a local continuity log, not project documentation. Read it
at the start of a session for context on recent work.
