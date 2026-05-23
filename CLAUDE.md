# equity-research-mcp

Locally-installable MCP server exposing equity-research tools over
Finnhub, yfinance, and SEC EDGAR. US equities only at MVP.

## What this is NOT
- Not a trading system, brokerage integration, or order executor
- Not a backtest engine or P&L tracker
- Not a screener or signal generator
- Not a sentiment analyzer (no social source ships in v0.1; see
  "Social source dropped from v0.1" below)

If a feature crosses this line, push back before building.

## Toolchain & layout
- Python 3.12
- pip + pyproject.toml. NEVER Poetry, pdm, conda, or `uv` as a package manager.
- venv in `.venv/` at repo root
- src layout: `src/equity_research_mcp/`
- Tests in `tests/` at repo root
- Build backend: hatchling
- Invoke venv binaries directly (`.venv/bin/python -m pip`, `.venv/bin/python -m pytest`). Do not rely on `source .venv/bin/activate` persisting between bash calls — shell state does not carry across separate tool invocations.

## Secrets & environment
All API keys come from shell environment, never from files in the repo.
Required env vars (each adapter checks at construction):
- `FINNHUB_API_KEY`
- `SEC_USER_AGENT`  (format: "Your Name your@email" — SEC requires this)
- `ANTHROPIC_API_KEY` — only when LLM critic lands (v0.3+); unused in v0.1

yfinance has no key. No social source ships in v0.1; if one is added
later it gets its own env var entry.

`.env.example` exists for documentation only. `.env*` files are
gitignored except `.env.example`. There is no `python-dotenv` dep —
shells export their own env.

## .gitignore was set up before any matching file
`.env`, `.env.*` (with `!.env.example`), `.venv/`, `.coverage`,
`__pycache__/`, `.pytest_cache/`, `.claude/settings.local.json`,
`dist/`, `build/`, `*.egg-info/`.

## Architecture conventions

### Single ClaudeClient gateway
All Anthropic API calls go through `equity_research_mcp.llm.ClaudeClient`.
No `anthropic.Anthropic()` instantiations anywhere else. v0.1 has zero
Anthropic calls — the gateway and the `llm/` folder land in v0.3 with
the LLM critic.

### Per-call budget via ContextVar
Each MCP tool call enters a fresh `Budget` context with named buckets.
Adapters call `current().charge(BUCKET_REQUESTS)` on each outbound HTTP
call. Exceeding a bucket's limit raises `BudgetExceeded` — hard fail.

v0.1 enforces only the `requests` bucket (external HTTP calls).
v0.3 adds the `tokens` bucket at the LLM gateway. Same `charge(bucket,
amount)` API — additive, no method-shape change.

### Source adapters as Protocol, not inheritance
See `src/equity_research_mcp/adapters/CLAUDE.md` for adapter rules. The
aggregator is source-unaware.

### Filesystem cache, content-hash + per-source TTL
JSON files under `~/.cache/equity-research-mcp/`. Key is SHA-256 of
(source, endpoint, params). TTL is per-source (price=1d, filings=1w,
news=4h, profile=1w). No SQLite, no Redis.

### No persistent state between MCP calls
Each tool call is stateless. Watchlists, alerts, "remember this from
yesterday" are out of scope — the MCP client's conversation context is
where memory lives.

### Tools surface frozen at 5 for v0.1
Four research tools (`get_company_profile`, `get_news`,
`get_price_action`, `get_recent_filings`) plus the aggregator
(`research_brief`). The diagnostic `health` tool is not counted.
Adding a tool requires explicit discussion of what it displaces. The
aggregator is the headline tool.

The original roadmap had six tools — the sixth slot was
`get_social_mentions` over a social source. It was deliberately dropped
from v0.1 after three source-tier failures (see below). The
`SourceAdapter` Protocol still declares `get_social_mentions` as a
documented extension seam; no v0.1 adapter implements it.

### Social source dropped from v0.1
Three source-tier checks failed during Phases 2–4:

1. **Finnhub `/stock/candle`** (Phase 2) — free tier returned 403.
   Pivoted price bars to yfinance.
2. **Reddit** (Phase 4 plan) — account permanently banned, no
   compliant API access path remained for the developer.
3. **StockTwits public stream** (Phase 4 replacement attempt) — fronted
   by a Cloudflare challenge page (HTTP 403, browser-fingerprinting
   challenge), unscriptable from a plain HTTP client.

Pattern: free, accessible, scriptable social-equity data is
consistently gated. The two headline signals (volume z-score, insider
Form 4 transactions) are live and verified without it. v0.1 ships
without a social source as a deliberate scope decision, not an
oversight.

The `SourceAdapter` Protocol is the documented extension point.
Architecture supports a social adapter; none ships in v0.1 because no
free source met the access and quality bar. A Reddit adapter via a
registered API app, a paid StockTwits Partner key, or a Bluesky firehose
client remain legitimate future additions if a compliant access path
opens.

### Source-tier verification meta-rule
Confirmed by the Phase 2 Finnhub pivot, the Phase 4 Reddit drop, and
the Phase 4 StockTwits drop: sanity-check every external data source
against its actual free-tier access BEFORE building its adapter, not
after. The Phase 3 EDGAR check (live probe + UA header sanity check
before code) was the first time the meta-rule paid off cleanly; the
subsequent social attempts surfaced their failures at the source-tier
step instead of after building.

## Workflow rules

This repo is private during development but **will go public**, and git
history is forever. Two hard constraints that override everything else:

1. **Never commit secrets.** No API keys, tokens, `.env` contents, or
   `SEC_USER_AGENT` values in any committed file — not in code, not in
   fixtures, not in tests, not in commit messages. `.gitignore` covers
   `.env*` (except `.env.example`) before any matching file exists. If a
   real secret would be needed to proceed, stop and ask; never inline a
   placeholder that looks real.

2. **Pip is gated by deps preview, not by per-command approval.** Before
   running `pip install` at the start of a phase, show the full proposed
   dependency list with a one-line justification per package. Dependency
   hygiene, not bureaucracy.

Standing approvals (encoded in `.claude/settings.local.json`):
- `Edit`, `Write`, `Read` — no per-file prompt
- All `git` subcommands (add, commit, branch, checkout, push, merge,
  tag, reset, rebase, switch, restore, stash, log, diff, status, show)
- `pip` install/uninstall/list/show/freeze (after the deps preview gate)
- `pytest`, `python`, standard read-only shell ops

Standing denials (also in `.claude/settings.local.json`):
- `git push --force*`, `git push -f`, `git push --force-with-lease*`
- `git reset --hard*`, `git clean -f*`
- `git config --global*` / `--system*`
- `rm -rf*`, `rm -fr*`, `rm -r *`, `sudo *`

When to stop and ask:
- An API key registration or app registration that requires you
- A scope decision (cutting/expanding a tool)
- A source-tier check that fails or returns a surprising shape
- Anything ambiguous that the plan or CLAUDE.md doesn't cover

Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`,
`refactor:`), one feature branch per phase (`phase/NN-short-name`),
PR with self-merge, annotated tag at release boundaries only.

## Testing scope
Tests cover: adapter normalization, aggregator logic, budget mechanics,
cache TTL/key behavior, error mapping. Excluded by design: FastMCP
server startup, thin tool decorators that just call into adapters, pure
I/O wrappers.

Adapter tests use saved JSON fixtures under `tests/fixtures/<source>/`.
No live network calls in tests. No VCR cassettes.

## Honest-language rule
No "production-ready", no "enterprise-grade", no "scales to millions",
no unverified performance numbers. Name tradeoffs explicitly. If a
limit is unknown, write "unmeasured" instead of guessing.

## Subagents available
- `code-reviewer` (sonnet) — runs after each phase, checks the diff
  against this CLAUDE.md, prioritized output (critical / warning /
  suggestion). Offline only; no network tools.
- `adapter-test-writer` (sonnet) — writes fixture-based pytest tests
  for a given adapter. Offline only; no live network calls.

## v0.1.0 deferred items

The v0.1.0 release explicitly defers 11 items from the code-review
carry-over track. Each is a stated decision, not a forgotten loose
end. Touching code that doesn't need fixing right before a release
tag is the riskiest possible time for unnecessary change; deferring
the prophylactic ones (P2.W3, P2.S4) is the same "no error handling
for can't-happen scenarios" discipline this CLAUDE.md applies
throughout, applied to the release boundary itself.

**Closed (no longer carry-over)**
- **P3.S4** — EdgarAdapter injected client missing `SEC_USER_AGENT`
  header. Resolved by design: the aggregator does not inject a shared
  client; each `_fetch_recent_filings` call constructs its own
  UA-headered adapter. The contract gap stayed theoretical.

**Deferred to v0.2** (most v0.1 carry-over)
- **P1.S1** — `@runtime_checkable` on `SourceAdapter` Protocol with
  no `isinstance` users. Intentional extension-seam tooling.
- **P1.S-NEW-1** — `data.get("written_at", 0)` epoch-0 sentinel.
  Functionally equivalent; bundle with any v0.2 cache-layer work.
- **P1.S-NEW-2** — `next(tmp_cache_dir.iterdir())` test brittleness.
  Revisit only if the cache file layout changes.
- **P2.W3** — `date.fromisoformat` `ValueError` unwrapped at tool
  boundary. FastMCP wraps it at runtime; adding a typed validation
  layer for a non-problem is the prophylactic defense the project
  rules out.
- **P2.S1** — `pstdev` vs `stdev` choice unannotated. Readability nit;
  context-documented in aggregator docstrings.
- **P2.S2** — Finnhub candle cache key uses Unix timestamps. UTC
  midnight is deterministic per calendar date; no correctness gap.
- **P2.S4** — yfinance `_bar_from_cache` defensive
  `.get("source", SOURCE)` fallback is unreachable. Dead-fallback
  cleanup; bundle into v0.2 polish.
- **P3.S1** — `_parse_form4_transactions` except tuple has `KeyError`
  + `TypeError` that are dead/broad. Intentional broad-catch at the
  per-filing graceful-degradation boundary.

**Deferred to v0.2+** (timing depends on related work)
- **P1.S5** — `SocialMention.score` non-sentiment field comment.
  Revise schema and field semantics when a real social adapter ships.
- **P3.S3** — `Filing.source = "edgar"` default is schema smell.
  Revise when a second filing source lands.

**Deferred to v0.3** (LLM critic phase)
- **P1.S2** — `BUCKET_TOKENS` constant unused. Reserved for the LLM
  gateway's tokens bucket.
