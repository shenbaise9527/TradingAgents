"""Test: Evaluate 沪电股份 (002463.SZ) using Tushare data vendor."""
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["backend_url"] = "https://api.deepseek.com/v1"
config["deep_think_llm"] = "deepseek-chat"
config["quick_think_llm"] = "deepseek-chat"
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

# Use Tushare for all data
config["data_vendors"] = {
    "core_stock_apis": "tushare",
    "technical_indicators": "tushare",
    "fundamental_data": "tushare",
    "news_data": "tushare",
}

config["output_language"] = "Chinese"

ta = TradingAgentsGraph(debug=True, config=config)

# Evaluate 沪电股份
_, decision = ta.propagate("002463.SZ", "2026-03-25")
print("\n" + "=" * 80)
print("FINAL DECISION for 沪电股份 (002463.SZ)")
print("=" * 80)
print(decision)
