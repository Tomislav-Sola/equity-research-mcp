# Adapters

Each data source implements the `SourceAdapter` Protocol in `protocol.py`.

## Rules

1. **Implement the Protocol fully.** No partial adapters. Every Protocol
   method must work or raise a typed exception from the
   `EquityResearchError` family (use `SourceCapabilityError` for
   "this source genuinely doesn't expose this kind of data"). Never
   return `None` to mean "not implemented." Never raise the built-in
   `NotImplementedError` — it isn't a project exception.

2. **Normalize, don't leak.** Adapters return shared schema instances
   from `equity_research_mcp.schemas`. Raw source payloads do not leave
   this layer. Source-specific fields that don't fit the schema are
   dropped — if they matter, extend the schema with discussion.

3. **Missing config = typed exception.** If an env var the adapter needs
   is unset, raise `MissingCredentials("VAR_NAME")` at construction
   time, not deep inside an HTTP call.

4. **Respect rate limits explicitly.** Each adapter declares its own
   limit (Finnhub 60/min, SEC 10/sec) and enforces it locally.
   Tenacity-based retry on 429 lands in v0.2; in v0.1, raise
   `RateLimited` immediately on 429 and let the caller decide.

5. **SEC User-Agent is required.** The EDGAR adapter reads
   `SEC_USER_AGENT` at construction and sends it on every request. Do
   not hardcode a default.

6. **Fixture-based tests are mandatory.** Every adapter ships with
   `tests/adapters/test_<source>.py` covering: happy path, missing
   credentials, 4xx mapping, normalization, malformed-payload. Fixtures
   live in `tests/fixtures/<source>/` as JSON.

7. **Budget integration.** Every outbound HTTP call charges the current
   budget's `requests` bucket: `current().charge(BUCKET_REQUESTS)`. The
   adapter does not decide the limit — that's the caller's context.
