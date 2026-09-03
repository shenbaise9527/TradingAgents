# Tushare 数据源接入设计（基于 v0.4.1 架构）

> 状态：待评审。基线 `main@9dee508`（v0.4.1）+ `feature/tushare-zh-support@824f49d`（已 rebase 的 7ec3f89）。
> 本文审视"如何基于最新架构更好地接入 tushare"，回答：当前实现与 v0.4.1 架构的差距、目标设计、文件级改动、测试与分期实施。

---

## 1. v0.4.1 数据层架构事实（接入前必须先对齐的契约）

| 机制 | 位置 | 语义 |
|---|---|---|
| 方法级 vendor 路由 | `dataflows/interface.py` `VENDOR_METHODS` + `route_to_vendor()` | 按 `data_vendors[category]` / `tool_vendors[method]`（method 优先）取 vendor **链**；显式配置绝不静默落到未选 vendor（#988/#289）；"default" 才用全部。已注册 tushare 8 方法 |
| 错误分类学 | `dataflows/errors.py` `VendorError` 树 | `NoMarketDataError`(含 symbol/canonical/detail) / `VendorRateLimitError` / `VendorNotConfiguredError`。**新 vendor 只子类化，路由层不需新 except**。全部失败后统一产出 `NO_DATA_AVAILABLE` 哨兵文本，禁止 agent 编造 |
| OHLCV 质量保证（yfinance 路径） | `dataflows/stockstats_utils.py` | `load_ohlcv`：5y 窗口、按分析日 point-in-time 截断（防未来函数）、同日内 TTL 刷新（#1150）、坏缓存重取、无收盘价最新 bar 拒绝（#1201）、gap 填充、stale 帧拒绝（#1021，NoMarketDataError）、缓存文件名 `safe_ticker_component` 防穿越（`dataflows/utils.py`） |
| 工具层 OHLCV 契约 | `y_finance.get_YFin_data_online` | 参数 `(symbol, start_date, end_date)` 先 `strptime` 校验；`normalize_symbol` → canonical；空 → `NoMarketDataError(symbol, canonical, detail)`；end 闭区间（+1 天）；`_assert_ohlcv_not_stale`；2 位小数；`# 表头 + CSV` 返回 |
| 校验快照 | `market_data_validator.py` → `stockstats_utils.load_ohlcv` | 市场分析师的"ground truth"工具 `get_verified_market_snapshot` **直连 yfinance 缓存路径，不经过 vendor 路由**（#830） |
| 符号规范 | `dataflows/symbol_utils.py` `normalize_symbol` | 纯语法、Yahoo 规范：别名表/加密/外汇规则。**尚无 A 股数字代码规则**。`benchmark_map` 已含 `.SS`/`.SZ`（上证综指 000001.SS、深成指 399001.SZ）；Yahoo 上证 = `.SS`，与 tushare 的 `.SH` 不一致 |
| 新闻安全 | `dataflows/date_window.py` `in_window` | 所有带日期内容统一 UTC 归一、半开窗 `[start, end+1天)`、无日期条目仅实盘保留（#992/#1007/#1126/#1220）；条数上限 `news_article_limit`/`global_news_article_limit` |
| 配置 | `dataflows/config.py` + `default_config.py` | `data_vendors`/`tool_vendors` 嵌套；env 覆盖 `_ENV_OVERRIDES` 只支持**扁平**键（无 data_vendors 行） |
| 可选依赖模式 | `pyproject.toml` | 先例：`[project.optional-dependencies] bedrock = [...]`（`pip install ".[bedrock]"`），核心安装保持精简 |
| 测试纪律 | `pyproject.toml [tool.pytest]` | markers：unit / integration / smoke；`--strict-markers` |

## 2. 当前 fork 实现与架构的差距（问题清单）

基于已 rebase 的 tushare 模块逐项对照：

1. **错误契约缺失（最关键）**：行情/基本面空结果返回**散文串**（如 `"No data found for symbol ..."`）而非 `NoMarketDataError` → 路由层视为"成功"，无法多 vendor 回退、不会产出统一 NO_DATA 哨兵，agent 可能把散文当事实。
2. **符号体系未接入框架**：`normalize_ts_code` 是独立副本；用户/上游其他路径（快照、benchmark、identity、CLI）用 Yahoo 规范 `.SS/.SZ`，tushare 用 `.SH/.SZ/.BJ`。`600000.SH` 在 yfinance 各路径全部失败（今日上游对 .SH 输入本身就不可用），而 `.SS` 规范输入反而两边都能服务。**需要一处双向桥接 + 统一 canonical。**
3. **质量保证管线缺失**：行情/指标用自研 CSV 缓存（15y 命名含当天窗口、无同日内 TTL、无 stale 守卫、无 `safe_ticker_component`、无复权处理 —— 直接返回未复权价，与 yfinance `auto_adjust=True` 的价格量级/除权跳变不一致，校验快照与行情工具会打架（正是 #830 要消灭的）。
4. **日期/时区**：无 A 股时区（Asia/Shanghai）概念；"今日 bar 是否收盘/是否需要刷新"语义与上游不一致。
5. **指标描述字典重复**：fork 在 `tushare_indicator.py` 复制了一份与 `y_finance.py` 相同的描述文案，后续上游增删指标会失步。
6. **依赖过重**：`tushare>=1.4.2` 进核心依赖；未装包且无 token 时的失败路径是裸 ImportError/ValueError，未映射到 `VendorNotConfiguredError`。
7. **新闻安全语义缺失**：tushare 新闻时间戳（CST）未做 UTC 归一/窗口截断；无条数上限。
8. **杂项**：根目录 `test_hudian.py` 是调试脚本残留；tushare_common 测试只覆盖纯函数，无任何 mock 化 vendor 路由/契约测试。

## 3. 设计目标 / 非目标

**目标**：让 tushare 成为与 yfinance/alpha_vantage 对等的"一等 vendor"——相同的错误语义、相同的质量保证、相同的工具输出形态；A 股用户一条配置即可切换；全量回归不破。

**非目标（防过度设计）**：
- 不把 `load_ohlcv`/校验快照路径改造成 vendor 感知（见 §5 决策 D，本期不做）。
- 不做 A 股指数/ETF/龙虎榜等扩展接口（本期仅股票日线+已实现 8 方法）。
- 不引入交易所日历包（交易日期以 tushare `trade_cal`/实际返回行为为准，本地只做 TZ 判断）。

## 4. 核心决策

### D1 符号：统一 canonical = Yahoo 规范（`.SS/.SZ`），tushare 内部转 `ts_code`

理由：
- 框架既有 CN 痕迹全部是 Yahoo 规范：`benchmark_map`（.SS/.SZ）、校验快照、identity、yfinance 默认源。让 tushare 反向适配成本最低。
- 用户输入任意形态（`600000` / `600000.SH` / `600000.SS`）都归一为同一 canonical：yfinance 系路径天然可用，tushare 系路径再转 `600000.SH` 请求。
- 深市 `.SZ` 两边同形，桥接只需管 `.SH↔.SS`（沪市）与 8/4 开头北交所（Yahoo `.BJ` 支持需线上验证；未验证前 BJ 仅 tushare 内部可用，快照/benchmark 对该类标的 NO_DATA，文档注明）。
- 落在 `symbol_utils.normalize_symbol`（纯语法、表驱动、与现有 crypto/forex 规则同构）：6 位纯数字或 `6位+.SH/.SS/.SZ/.BJ`，按首位映射：`6→.SS`、`0/3→.SZ`、`8/4→.BJ`（`.SH→.SS` 视为输入规整）。加行即可扩展。
- tushare 侧新增 `to_ts_code(canonical)`：`.SS→.SH`、`.SZ→.SZ`、`.BJ→.BJ`；非 CN 代码/指数代码 → 快速 `NoMarketDataError`（不消耗 API 配额，路由链安静跳过——解决"默认链混入 tushare 时 US 标的每次打点/日志噪音"问题）。

### D2 错误：全面子类化 `errors.py`，零新增 except

- `TushareRateLimitError(VendorRateLimitError)`、`TushareNotConfiguredError(VendorNotConfiguredError)`（已落地于 rebase 提交）。
- 补：`tushare_api_call` 错误映射表——频控词（频率/每分钟/limit/credits…）→ RateLimit；**权限/积分词（权限/积分/无该接口…）→ TushareNotConfiguredError**（账户无该接口授权，路由视为"该 vendor 不可用"并带明细继续/上抛）；空结果与行情无数据 → `NoMarketDataError(symbol, canonical, detail)`；`import tushare` 失败 → TushareNotConfiguredError（提示装 `[tushare]` extra）。

### D3 行情/指标：复刻上游 OHLCV 保证管线（复用，不重写）

- `tushare_stock.get_stock`：参数 strptime 校验 → `to_ts_code` → `ts.pro_bar(adj="qfq")`（**前复权**，与 yfinance `auto_adjust` 口径对齐）→ 空 → `NoMarketDataError` → `_assert_ohlcv_not_stale(data, end_date, symbol, canonical)`（从 `stockstats_utils` 直接导入，**不复制实现**）→ 2 位小数 → 与 `get_YFin_data_online` 同构的 `# 表头（含 resolved 标注）+ CSV`。
- `tushare_indicator`：先取历史日线（qfq）→ `stockstats_utils._clean_dataframe` + `stockstats.wrap` 同一套 → 输出形态与 `get_stock_stats_indicators_window` 对齐；缓存复用 `data_cache_dir` + `safe_ticker_component` + 与上游同 TTL 的同日刷新语义；**curr_date point-in-time 截断**。
- **指标描述单一来源**：把 `y_finance`/fork 重复的 `_INDICATOR_DESCRIPTIONS` 提升到 `stockstats_utils.py`，`y_finance.py` 与 `tushare_indicator.py` 共同 import（纯搬移，不改变任何行为）。
- 缓存命名 `{safe}-Tushare-...` 与 yfinance 命名空间隔离，避免互踩。

### D4 校验快照路径：本期不改 vendor 感知，靠 D1+D3 对齐口径

- 现状：`get_verified_market_snapshot` → `load_ohlcv`（yfinance 硬编码）。改造它要动 #1021/#1150/#1201/#986 等核心保证及其测试，风险高。
- 本期策略：A 股 canonical（.SS/.SZ）在 yfinance 快照路径**天然可用**；且 D3 让 tushare 行情输出前复权，两个来源价格口径一致 → 工具间不打架。快照仍走 yfinance（CN 用户网络可达 Yahoo 时一致；不可达时快照 NO_DATA，文档注明降级）。
- 备选（评估后另立设计，不并入本期）：`load_ohlcv` 按 `data_vendors.core_stock_apis` 分发原始 df 获取器，缓存/守卫层保持 vendor 无关——收益是"纯 tushare 内网环境"也能快照，代价是触碰核心保证。

### D5 新闻/内幕：对齐 yfinance_news 的安全语义

- tushare 新闻时间戳（东八区）→ UTC（`date_window.to_utc`）→ `in_window(start,end)` 窗口截断（防 #992/#1007 式未来泄漏）；空 → 与 `yfinance_news` 同方法的输出习惯一致（散文 + 明确"no news"）；遵守 `news_article_limit`/`global_news_article_limit`。
- 无日期/纯实时流的接口若 tushare 返回的是"公告/快讯"类，保留其日期语义，绝不把无日期条目塞进回测窗口。

### D6 依赖与配置

- `pyproject.toml`：`tushare>=1.4.2` 移出核心依赖 → 新增 optional extra `tushare = ["tushare>=1.4.2"]`（模式同 bedrock）。未装包时行为见 D2。
- 配置：沿用 `data_vendors`（示例 `"core_stock_apis": "tushare,yfinance"`，顺序即回退链）；`tool_vendors` 可单方法覆盖。`_ENV_OVERRIDES` 不加行（本期仅程序化/代码配置；CLI 交互选择属 Phase 2）。
- 文档：CLAUDE.md 增补 CN 使用说明（符号规范、配置示例、token、局限）；删根目录 `test_hudian.py`（如有独有断言并入 tests/）。

## 5. 文件级改动清单

| 文件 | 动作 |
|---|---|
| `dataflows/symbol_utils.py` | +A 股规则（6 位数字 → .SS/.SZ/.BJ；.SH→.SS 规整）+ `is_cn_equity_like()` 纯语法判定 |
| `dataflows/tushare_common.py` | +`to_ts_code`/错误映射表/`import tushare` 守卫/沪 TZ 工具（`zoneinfo` "Asia/Shanghai"） |
| `dataflows/tushare_stock.py` | 重写为 D3 契约 |
| `dataflows/tushare_indicator.py` | 重写为 D3；删除复制字典改 import 共享源 |
| `dataflows/stockstats_utils.py` | 指标描述字典提升至此（搬移） |
| `dataflows/y_finance.py` | 改为 import 共享指标描述字典（搬移，无行为变化） |
| `dataflows/tushare_fundamentals.py` | 错误契约化（D2）+ 空值/格式与同方法 yfinance 对齐 |
| `dataflows/tushare_news.py` | 窗口/UTC/条数上限（D5）+ 错误契约化 |
| `dataflows/interface.py` | 无逻辑改动（已注册）；核对 `VENDOR_LIST` 是否死代码 |
| `pyproject.toml` | tushare → optional extra |
| `tests/test_tushare_common.py` | 更新/扩展（桥接、错误映射、TZ 工具） |
| `tests/`（新增） | `test_tushare_stock.py`、`test_tushare_vendor_routing.py`（mock pro_api/路由）、`test_symbol_utils` A股规则用例 |
| `CLAUDE.md` | CN 使用章节 |
| `test_hudian.py` | 删除（或并入测试） |

## 6. 测试计划（TDD，先测试后实现）

- **单元（无网络）**：日期/时区工具；`to_ts_code` 双向桥接（.SS/.SH/.SZ/.BJ、指数、非 CN）；错误映射关键词表；缓存文件名安全。
- **路由集成（mock，无网络）**：patch `tushare_common.get_pro_api`（假 pro 对象返回罐头 DataFrame）：行情格式化头 + qfq 列、空 → NoMarketDataError、stale → NoMarketDataError、配置 `data_vendors=core_stock_apis: "tushare,yfinance"` 时首 vendor 失败干净回退 yfinance、无 token → VendorNotConfigured → 回退、US 代码配 tushare → 快速 NoMarketDataError（不触网）。
- **回归**：全量 pytest（当前 664+ 用例基线全绿）；`test_tushare_common.py` 既有断言保持通过或同步迁移。
- **人工验收（需真实 token，不在本环境）**：A 股实跑冒烟（`600519` / `000001.SZ` 全流程），核对 tushare 与快照口径。

## 7. 分期实施（评审通过后按此推进，每步 TDD + 评审）

- **P1 契约与符号（地基）**：D1 symbol_utils 规则 + 测试；D2 错误映射 + `to_ts_code` + TZ 工具 + 测试。→ 评审点 1
- **P2 行情/指标对齐**：D3（stock/indicator 重写 + 描述字典搬移 + mock 测试）。→ 评审点 2
- **P3 基本面/新闻/内幕对齐**（D2/D5）。→ 评审点 3
- **P4 收尾**：pyproject extra、CLAUDE.md、删 test_hudian、全量回归。→ 评审点 4
- **Phase 2（另行决策）**：快照 vendor 感知（D4 备选）、指数/ETF、CLI 数据源选择、env 行。

## 8. 残余风险

1. 本环境无 Tushare token/网络，行为正确性只能靠 mock + 全量回归兜底；**真实 API 语义（错误消息关键词、pro_bar qfq 行为、北交所 .BJ、新闻接口权限）需线上冒烟确认**——关键词表用宽松匹配 + 覆盖关键词注释，线上核验后收紧。
2. Yahoo `.BJ`（北交所）支持度未验证；`.BJ` canonical 化若与 Yahoo 冲突，回退为"仅 tushare 内部支持"并文档注明。
3. `stockstats_utils`/`y_finance` 的搬移属共享代码触碰，用纯搬移 + 全量回归控制风险。
