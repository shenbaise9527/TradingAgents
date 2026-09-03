# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAgents is a multi-agent LLM framework that simulates a trading firm's decision-making process. Specialized AI agents (analysts, researchers, traders, risk managers) collaborate through structured debates to produce trading decisions with a five-tier rating scale: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL.

This branch (`feature/tushare-zh-support`) adds **Tushare as a China A-share data vendor**, aligned with the upstream v0.4.1 data-layer architecture (error taxonomy, vendor routing, OHLCV guarantee pipeline). See `docs/tushare-data-source-design.md` for the design.

## Build & Run Commands

```bash
# Install (uses uv for dependency management)
uv pip install -e .

# With Tushare A-share support
uv pip install -e ".[tushare]"

# Run CLI (interactive mode)
tradingagents

# Run programmatically
python main.py

# Run tests
pytest tests/

# Run the Tushare-specific test set
pytest tests/test_tushare_common.py tests/test_tushare_stock.py \
       tests/test_tushare_indicator.py tests/test_tushare_fundamentals.py \
       tests/test_tushare_news.py tests/test_tushare_vendor_routing.py

# Lint
ruff check tradingagents/ tests/
```

## Required Environment Variables

API keys are loaded from `.env` (see `.env.example`). At minimum, one LLM provider key is needed:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`
- Optional: `ALPHAVANTAGE_API_KEY` (if using Alpha Vantage as data vendor; yfinance is the default and needs no key)
- Optional: `TUSHARE_API_TOKEN` **or the `TUSHARE_TOKEN` alias** (Tushare A-share vendor; register at https://tushare.pro). A missing token or package is a *typed* "vendor not configured" error, never a crash.

## Architecture

### Agent Pipeline (LangGraph StateGraph)

The system executes a fixed pipeline with configurable debate rounds:

```
Analysts (Market → Social → News → Fundamentals)
    ↓
Research Debate (Bull ↔ Bear, N rounds) → Research Manager
    ↓
Trader
    ↓
Risk Debate (Aggressive ↔ Conservative ↔ Neutral, N rounds) → Portfolio Manager
    ↓
Final Decision (five-tier rating)
```

### Key Modules

- **`tradingagents/graph/`** — LangGraph orchestration. `TradingAgentsGraph` is the main entry point. `GraphSetup` wires all nodes and edges. `ConditionalLogic` controls debate round routing.
- **`tradingagents/agents/`** — Agent node functions. Each agent is a factory function (e.g., `create_market_analyst()`) returning a LangGraph node callable. State is defined in `agent_states.py` as TypedDicts.
- **`tradingagents/agents/memory.py`** — `FinancialSituationMemory` using BM25 for lexical similarity retrieval of past trading situations. No embeddings or external vector DB.
- **`tradingagents/dataflows/`** — Data vendor abstraction. `interface.py` routes tool calls via `VENDOR_METHODS` (yfinance, alpha_vantage, fred, polymarket, **tushare**) using `data_vendors[category]` defaults with `tool_vendors[method]` overrides. `route_to_vendor()` semantics: the configured list **is** the fallback chain (never silently pulls in unconfigured vendors); a `"default"` sentinel uses every registered vendor.
- **`tradingagents/dataflows/errors.py`** — `VendorError` taxonomy (`NoMarketDataError`, `VendorRateLimitError`, `VendorNotConfiguredError`). Vendors subclass these; the router needs no per-vendor `except` clauses. When every vendor fails, the router emits one `NO_DATA_AVAILABLE`/`DATA_UNAVAILABLE` sentinel — agents must report unavailability, never fabricate.
- **`tradingagents/llm_clients/`** — Multi-provider LLM factory; supports OpenAI, Anthropic, Google, xAI, DeepSeek, Qwen, GLM, Azure, Bedrock, Ollama, OpenRouter, and arbitrary OpenAI-compatible endpoints.
- **`cli/`** — Typer CLI with Rich UI. Has its own interactive LLM provider / output-language selection; data-vendor selection is config-driven (below).

### Configuration

All config lives in `tradingagents/default_config.py` as `DEFAULT_CONFIG` dict (overridable via `TRADINGAGENTS_*` env vars for the flat keys). Key settings:
- `llm_provider` / `deep_think_llm` / `quick_think_llm` — LLM selection
- `max_debate_rounds` / `max_risk_discuss_rounds` — debate depth
- `output_language` — e.g. `"Chinese"`; upstream `get_language_instruction()` localizes every agent that writes the report
- `data_vendors` — category-level data source selection (e.g. `"core_stock_apis": "tushare,yfinance"` for chain fallback)
- `tool_vendors` — per-tool overrides (highest priority)
- Provider-specific thinking params: `google_thinking_level`, `openai_reasoning_effort`, `anthropic_effort`

## Tushare (China A-share) data source

### Enabling it

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["data_vendors"] = {
    "core_stock_apis": "tushare",        # or "tushare,yfinance" to fall back
    "technical_indicators": "tushare",
    "fundamental_data": "tushare",
    "news_data": "tushare",
}
config["output_language"] = "Chinese"    # optional: localize the report

graph = TradingAgentsGraph(config=config)
_, decision = graph.propagate("002463.SZ", "2026-03-25")
```

### Symbol conventions (important)

- **Canonical form is Yahoo-style** (`.SS` Shanghai / `.SZ` Shenzhen / `.BJ` Beijing) because the framework's yfinance-touching paths — verified-market snapshot, instrument identity, benchmark map — use it. `symbol_utils.normalize_symbol` maps bare 6-digit codes and `.SH` input to it (e.g. `600000` and `600000.SH` both become `600000.SS`).
- Tushare converts internally to its own `ts_code` (`.SH/.SZ/.BJ`, derived from the leading digit: 6→SH, 0/3→SZ, 8/4→BJ) via `tushare_common.normalize_ts_code`.
- Non-A-share symbols raise `NoMarketDataError` before any API spend, so a tushare entry in a fallback chain is silent for US/HK symbols.
- `.BJ` (Beijing) codes work through Tushare; the Yahoo-side snapshot path may not cover them (see design doc).

### Guarantees the tushare modules honour

- **Prices are qfq (前复权)** via `pro_bar(adj="qfq")`, aligning with the yfinance `auto_adjust` data the verified-market snapshot uses, so cross-vendor price claims don't fight each other.
- **Look-ahead safety**: indicator/OHLCV history is trimmed to the analysis date *before* computation; news uses the shared `date_window.in_window` UTC window.
- **Staleness guard**: a frame whose latest bar is far older than the requested date is rejected (`NoMarketDataError`) — same rule as yfinance (`_assert_ohlcv_not_stale`).
- **Caching**: 5-year daily history cached under the configured `data_cache_dir` (default `~/.tradingagents/cache/`) with same-day refresh TTL semantics shared with yfinance.
- **Typed failures**: rate/credit limits → `TushareRateLimitError`; missing token/package or per-interface permission (`权限`/`积分` messages) → `TushareNotConfiguredError`; no data → `NoMarketDataError`. Keyword classification lives in `tushare_common`.

### Known limitations

- Tushare's general `news` interface usually requires extra account permission; `news_data: "tushare"` then degrades via the router (configure `"tushare,yfinance"` if you want a fallback).
- Ticker news is filtered locally from the general stream by code/name — coverage is sparser than Yahoo; empty windows report honestly.
- `stk_holdertrade` (大股东增减持) replaces US-style insider filings; empty results are normal and reported as plain text.
- A-share indices/ETFs are not covered by the equity `daily` path in this branch.

## Conventions

- Agents use LangChain's tool-calling pattern with `ToolNode` for execution.
- Message history is cleared between pipeline stages (`create_msg_delete()` in `agent_utils.py`) with a context-anchored placeholder (never a bare "Continue").
- Ticker symbols preserve exchange suffixes (e.g., `0700.HK`, `600519.SS`) via `build_instrument_context()`.
- The `reflect_and_remember()` flow runs post-trade to store lessons in BM25 memory.
- The vendor-router never silently returns an unconfigured vendor's data (#988/#289); multi-vendor fallback is explicit (`"a,b"` order).
- TDD is expected: see the `tests/test_tushare_*.py` files for the mock-at-the-`fetch_daily_bars`/`get_pro_api`-seam pattern (offline, no API credits).
