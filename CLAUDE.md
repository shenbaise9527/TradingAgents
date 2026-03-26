# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAgents is a multi-agent LLM framework that simulates a trading firm's decision-making process. Specialized AI agents (analysts, researchers, traders, risk managers) collaborate through structured debates to produce trading decisions with a five-tier rating scale: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL.

## Build & Run Commands

```bash
# Install (uses uv for dependency management)
uv pip install -e .

# Run CLI (interactive mode)
tradingagents

# Run programmatically
python main.py

# Run tests
pytest tests/

# Run specific test
pytest tests/test_ticker_symbol_handling.py
```

## Required Environment Variables

API keys are loaded from `.env` (see `.env.example`). At minimum, one LLM provider key is needed:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `XAI_API_KEY`
- Optional: `ALPHAVANTAGE_API_KEY` (if using Alpha Vantage as data vendor; yfinance is the default and needs no key)
- Optional: `TUSHARE_API_TOKEN` (if using Tushare as data vendor for Chinese A-share market data; register at https://tushare.pro)

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
- **`tradingagents/agents/`** — Agent node functions. Each agent is a factory function (e.g., `create_market_analyst()`) returning a LangGraph node callable. State is defined in `agent_states.py` as TypedDicts (`AgentState`, `InvestDebateState`, `RiskDebateState`).
- **`tradingagents/agents/memory.py`** — `FinancialSituationMemory` using BM25 for lexical similarity retrieval of past trading situations. No embeddings or external vector DB.
- **`tradingagents/dataflows/`** — Data vendor abstraction. `interface.py` routes tool calls to vendor implementations (yfinance, Alpha Vantage, or Tushare) based on config. Routing priority: tool-level override > category-level default. Tushare modules (`tushare_*.py`) handle Chinese A-share data with automatic ticker normalization (SH/SZ/BJ suffixes), date format conversion, and chunked API fetching for large datasets.
- **`tradingagents/llm_clients/`** — Multi-provider LLM factory. `create_llm_client()` routes to OpenAI, Anthropic, or Google client. Also supports xAI, Ollama, and OpenRouter via OpenAI-compatible wrapper.
- **`cli/`** — Typer-based CLI with Rich UI for interactive analysis. Entry point: `cli/main.py`.

### Configuration

All config lives in `tradingagents/default_config.py` as `DEFAULT_CONFIG` dict. Key settings:
- `llm_provider` / `deep_think_llm` / `quick_think_llm` — LLM selection
- `max_debate_rounds` / `max_risk_discuss_rounds` — debate depth
- `data_vendors` — category-level data source selection (yfinance, alphavantage, tushare)
- `tool_vendors` — per-tool data source overrides (same vendor options)
- Provider-specific thinking params: `google_thinking_level`, `openai_reasoning_effort`, `anthropic_effort`

### Conventions

- Agents use LangChain's tool-calling pattern with `ToolNode` for execution
- Message history is cleared between pipeline stages for Anthropic compatibility (`create_msg_delete()` in `agent_utils.py`)
- Ticker symbols preserve exchange suffixes (e.g., `0700.HK`) via `build_instrument_context()`
- The `reflect_and_remember()` flow runs post-trade to store lessons in BM25 memory
- Data is cached locally in `tradingagents/dataflows/data_cache/`
- Vendor-specific rate-limit errors (`AlphaVantageRateLimitError`, `TushareRateLimitError`) trigger automatic fallback to the next vendor in `route_to_vendor()`
- Tushare ticker normalization: bare numeric codes are mapped to exchanges by prefix (6→SH, 0/3→SZ, 8/4→BJ); existing suffixes like `.SH` are preserved
