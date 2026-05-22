# equity-research-mcp

Locally-installable MCP server exposing equity-research tools (filings,
price, news, social) over Finnhub, SEC EDGAR, and Reddit. Six MCP tools
for US-equity research. Mount in Claude Desktop or Claude Code.

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

## Development

See [CLAUDE.md](CLAUDE.md) for project conventions.
