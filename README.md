# equity-research-mcp

Locally-installable MCP server exposing equity-research tools (filings,
price, news) over Finnhub, yfinance, and SEC EDGAR. Five MCP tools for
US-equity research. Mount in Claude Desktop or Claude Code.

**Status: pre-v0.1, under active development.** Full usage instructions
land with v0.1.0.

## What this is NOT

Not a trading system. Not a screener. Not a backtest engine. Not a
sentiment analyzer. Research-side only.

## Data sources

| Tool | Source | Notes |
|---|---|---|
| `get_company_profile` | Finnhub | Free tier sufficient. `FINNHUB_API_KEY` required. |
| `get_news` | Finnhub | Free tier sufficient. |
| `get_price_action` | yfinance | Unofficial Yahoo Finance client, best-effort. No API key. Yahoo occasionally changes its endpoints, in which case `yfinance` breaks until upstream patches it. The original plan was Finnhub for price bars, but `/stock/candle` is paid-tier only, which would have left price action broken on the standard free-tier setup. |
| `get_recent_filings` | SEC EDGAR | Free, public. `SEC_USER_AGENT` required (format: "Your Name your@email"). Form 4 includes parsed insider transactions; 8-K / 13G / 13D return metadata only. |
| `research_brief` | Aggregator | Fans out over the four sources above; deterministic correlation flags. No LLM in v0.1. |

No social source ships in v0.1. The roadmap originally included a sixth
tool (`get_social_mentions`) over a free social feed, but three
source-tier checks failed in sequence: Finnhub free-tier limits, Reddit
access issues, and StockTwits' Cloudflare gate. The `SourceAdapter`
Protocol still declares the capability as a documented extension seam
so a future Reddit-via-registered-app, paid StockTwits, or Bluesky
firehose adapter can land without architectural change. See
[CLAUDE.md](CLAUDE.md) for the full rationale.

## Development

See [CLAUDE.md](CLAUDE.md) for project conventions.
