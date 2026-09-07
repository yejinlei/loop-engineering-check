# 框架差异矩阵

M 层的价值全部在这一份文件里。**每个框架的每一格都必须四选一**，不允许含糊：

- `框架已保证` — 结构上已保证，无需自加（要说明框架在哪个层保证的）
- `默认不安全需自加` — 框架不提供，你必须自己写
- `框架特有风险` — 框架自带一个坑，用不用都会踩，除非你显式处理
- `不适用` — 该框架不存在这个概念

> **重要**：下表中的 API 名是**机制定位线索**，不是断言。运行时必须用 SKILL.md 第 1 步的自省拿本机真实签名核对；不一致时以自省为准，并把差异记入报告。标注 `[待确认]` 的条目在自省跑通前不要写成「存在缺陷」。

**证据等级说明**（2026-09-07 核验后）：

- `[源码确认]` — 直接读到上游仓库源码，标注文件与行号。最强，但仍可能与你本机版本不同。
- `[官方文档确认]` — 读到官方文档原文，标注 URL 与页面所述版本。
- `[版本知识]` — 未核验，来自预编码经验。
- `[待确认]` — 核验尝试过但未拿到定论，不做断言。

**核验范围**：LangGraph、OpenAI Agents SDK、Google ADK、smolagents、LlamaIndex、Crawl4AI 的关键分歧点已按 `[源码确认]` / `[官方文档确认]` 更新并就地注明来源。AutoGen / AG2 未能核验（文档站结构已重构，见该节）。deepagents 无独立核验，继承 LangGraph 结论。核验记录见本文件末尾。

> **核验推翻了本表早期版本的 10 处结论**，逐处已就地标注 `⚠ 早期版本写 X 是错的`。这不是修辞：本技能之所以把「自省 + 证据等级」设成硬约束，就是因为凭记忆写的框架结论会错，而且**错的方式往往和直觉相反**——下面几处早期版本全都写「更省事」或「更安全」，实际都更危险：LangGraph 的并发写会**炸**而不是静默丢状态；OpenAI SDK 的 handoff **会**共享预算而不是不继承；smolagents 超限会给**编造的完成品**而不是半成品；LlamaIndex **根本没有** `max_steps`。

---

## 目录（本文件 500+ 行，只读你命中框架的那一节）

| 节 | 内容 | 证据等级 |
|---|---|---|
| LangGraph | Pregel superstep 计数、并发写会炸 | `[官方文档确认]` 最扎实 |
| LangChain | 上层封装，不新增 loop 语义 | `[版本知识]` |
| deepagents | 继承 LangGraph 的推断 | **未核验**，`[待确认]` |
| Google ADK | template workflow 与 2.0 graph workflow 分叉 | `[官方文档确认]` 关键分歧点 |
| Crawl4AI | 无 agentic 策略；价值在 M13 与 L 层 | `[官方文档确认]` |
| OpenAI Agents SDK | `max_turns` run 全局、guardrail 时序 | `[官方文档确认]` |
| AutoGen / AG2 | **整表未核验**，三代 API 语义不同 | `[待确认]` |
| LlamaIndex | `max_steps` 不存在，M1/M3 已下调 | `[源码确认]` + `[官方文档确认]` |
| smolagents | 超限合成「看起来完整」的答案 | `[源码确认]` |
| 自研 loop | 无框架兜底的主路径 | — |
| 如何新增一个框架 | 模板 | — |
| 核验记录（2026-09-07） | 已核验 / 未核验 / 网络可达性 | — |

> 每个框架节末尾都有对应的自省片段；「如何新增一个框架」是补录模板，不要当成已有框架读。

---

## LangGraph（langgraph）

> 证据等级 `[官方文档确认]`（docs.langchain.com / docs.langchain.com/oss/python/langgraph/，页面提供 llms.txt 与逐页 Markdown，2026-09-07 核验）。这是本文件核验最扎实的一张表。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证`（有上限参数） | 存在轮次/递归上限参数，但**默认值偏宽**，且只限轮次 |
| M2 | `默认不安全需自加` | 上限只在 `invoke` 入口生效一次，内部嵌套循环不受约束 |
| M3 | **`框架特有风险`** | 计数单位是 **step（即 Pregel 的一个 superstep）**，不是「节点执行次数」，也不是 LLM 调用数。一个 step 是「计划→并行执行→更新」的完整三阶段，内部可含**多个并行节点**。换算比是**多对一且不可预测**：单线路径下 1 step ≈ 1 次节点执行（≈ 2:1 于 LLM 调用），但并行分支下 1 step 可含 N 次节点执行。`[官方文档确认]` pregel.md「Pregel organizes the execution of the application into multiple steps… Repeat until no actors are selected for execution, or a maximum number of steps is reached」+ GRAPH_RECURSION_LIMIT.md「reached the maximum number of steps」。**⚠ 早期版本写「数节点执行次数、ReAct 一步≈2 节点执行」是错的**：把 node 数当预算会同时高估和低估，取决于图里有没有并行分支 |
| M4 | `框架已保证`（硬抛异常） | 超限抛 `GraphRecursionError`。优点是能看见，缺点是**整个 invoke 挂掉**，你需要自己决定这算失败还是部分成功 |
| M5 | `默认不安全需自加` | **框架不拦工具名**。LLM 返回未注册工具名时，`ToolNode` 把错误塞回 message，不报错——agent 继续跑，基于错误状态 |
| M6 | `默认不安全需自加` | 无内建参数 schema 校验闸门；错误同样只进 message |
| M7 | `默认不安全需自加` | 框架不校验 tool_call arguments 的 JSON 闭合性 |
| M8 | `框架已保证`（默认进 message） | 工具失败会进 message，但**不会终止**。你要自己决定是否终止 |
| M9 | **`框架特有风险`** | reducer 语义决定并发写入行为。用覆盖语义（`LastValue` 类）+ 并行分支写同一 key = **抛 `INVALID_CONCURRENT_GRAPH_UPDATE` 异常**（`[官方文档确认]` INVALID_CONCURRENT_GRAPH_UPDATE.md：「if multiple nodes in e.g. a fanout within a single step return values for "some_key", the graph will throw this error」），不是「静默丢状态」。修法：给该 key 挂 reducer，如 `Annotated[list, operator.add]`。**⚠ 早期版本写「静默丢状态、无任何告警」是错的**：实际会炸，但结论要反过来读——故障是**可用性**问题（整个 step 失败），不是数据正确性问题。真正的静默风险在别处：**无 reducer 的默认 channel 是覆盖语义**（pregel.md「LastValue is the default channel type. It stores the last value written to it, overwriting any previous value」），单节点写会无声覆盖 |
| M10 | `默认不安全需自加` | 流式与完整性的检查不在框架层 |
| M11 | `框架已保证`（有重试机制，**且默认分类错误**） | 参数是 `add_node(..., retry_policy=RetryPolicy(...))`，`from langgraph.types import RetryPolicy`；默认 `max_attempts=3`、`initial_interval=0.5`、`backoff_factor=2.0`、`max_interval=128.0`、`jitter=True`。默认 `default_retry_on` **区分可重试错误**：对 requests/httpx 类异常**只重试 5xx**，并排除 `ValueError` / `TypeError` / `RuntimeError` / `OSError` 等约 11 类。另有 `timeout=`（`TimeoutPolicy(run_timeout=, idle_timeout=, refresh_on=)`）与 `error_handler=`（重试耗尽后跑补偿，可返回 `Command` 路由到别处），后两者需 `langgraph>=1.2`。**⚠ 早期版本写「默认不区分可重试/不可重试错误」是错的**——这是框架做得比预期好的一项。审计重点相应变化：不是「有没有分类」，而是**自定义 `retry_on` 时有没有放宽默认排除名单**（比如为了重试网络故障把 `OSError` 或裸 `Exception` 加进去）。`[官方文档确认]` fault-tolerance.md |
| M12 | `默认不安全需自加` | 输出结构化需要自己配 structured output |
| M13 | `默认不安全需自加` | 无内建注入防护、无工具权限分级 |
| M14 | `框架已保证`（可配置） | 可通过 config 传 seed |
| L2 | **`框架特有风险`** | 子图（subgraph）的上限需要**显式传递**；子图是否默认继承宿主的值直接决定 L2 预算下传是否生效。子图另有 checkpointer 继承模式：per-invocation（默认，每次调用全新状态）/ per-thread / stateless（`[官方文档确认]` use-subgraphs.md）。子图并发调用还有独立坑：官方警告 per-thread 子图被并行调用会产生 checkpoint 冲突，需用 `langchain.agents.middleware.ToolCallLimitMiddleware(tool_name=..., run_limit=1)` 防并行（同页）——纯 `StateGraph` 自建图没有这个中间件，得自己配模型禁用并行工具调用 |
| L5 | `框架已保证` | Checkpointer 是内建的，这是 LangGraph 的强项 |
| L7 | `框架已保证`（有 interrupt 机制） | `from langgraph.types import interrupt` 可在节点内任意位置暂停；恢复用 `Command(resume=...)`，必须配 checkpointer + `config={"configurable": {"thread_id": ...}}`。**注意副作用**：官方明确「The node restarts from the beginning of the node where the interrupt() was called when resumed, so any code before the interrupt() runs again」——`interrupt()` 之前已执行的代码会重跑。把 `interrupt()` 放在写库/发消息之后 = L5/L6 的重复副作用。`[官方文档确认]` interrupts.md |

### LangGraph 自省

```python
import inspect
import langgraph
print("langgraph version:", getattr(langgraph, "__version__", "?"))
from langgraph.graph import StateGraph
print("StateGraph:", inspect.signature(StateGraph))
# 关键：确认 recursion_limit 的默认值和计数单位（单位是 step/superstep）
try:
    from langgraph.pregel import Pregel
    print("Pregel init:", inspect.signature(Pregel.__init__))
except Exception as e:
    print("Pregel introspect failed:", e)
# 已确认存在，但仍应核对本机版本是否一致
try:
    from langgraph.types import RetryPolicy, TimeoutPolicy, interrupt, Command
    from langgraph.errors import NodeError
    print("RetryPolicy:", inspect.signature(RetryPolicy))
    print("TimeoutPolicy:", inspect.signature(TimeoutPolicy))
except Exception as e:
    print("langgraph types introspect failed:", e)
```

> 版本敏感：`timeout=` / `error_handler=` / `NodeTimeoutError` / `NodeError` 需 `langgraph>=1.2`。低版本项目审计时不要把这些当成可用手段。`Runtime.execution_info.node_attempt` 在不配 retry policy 时也可用（默认为 1），可用来检测「已经重试过」而切降级路径。

---

## LangChain（langchain）

> **不占独立矩阵。** `AgentExecutor` / `create_react_agent` 属于旧式 agent 抽象，loop 语义现在基本让位给 LangGraph。审计时写「继承 LangGraph 结论，除下列除外」即可，不要复制整张表。
>
> 唯一值得单列的例外：`AgentExecutor(max_iterations=15, max_execution_time=60)` 数的是 **agent 迭代**（一次模型调用 + 一次工具调用算一轮），换算比约 1:1，接近 ADK 的 iteration 语义，**不同于** LangGraph 的节点执行口径。这是历史上最容易混淆的一处：同一项目里 `AgentExecutor` 和 LangGraph 图混用时，两个 limiter 单位不同，L2 预算下传极易错。`[版本知识]`

---

## deepagents（LangChain 社区推出的 deepagents）

> 证据等级 `[版本知识]`，**未独立核验**（2026-09-07）：本次核验只覆盖了 LangGraph 本身，没有单独拉 deepagents 的文档或源码。所以本表是「继承 LangGraph」的推断，凡标 `[待确认]` 的推断点**必须以自省结果为准**，不要把本表当断言。deepagents 是 LangChain 社区官方推出的 agentic 框架，不是 LangGraph 的 fork。

> 继承 LangGraph，所以 LangGraph 的所有结论**继承生效**，除非下表明示不同。审计时必须确认版本继承关系——deepagents 升级后 LangGraph 行为可能同步变化。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证`（继承） | 继承 LangGraph 的上限参数 |
| M3 | **`框架特有风险`** | 继承的坑 + **中间件层可能再加一层轮次控制**。两层 limiter 同时生效时**哪个先触发、报错信息是什么**是 deepagents 独有的问题 |
| M5 | `框架已保证`（部分） | 子 agent 委派机制内建；但被委派 agent 的工具白名单是否强制隔离需确认 |
| M9 | **`框架特有风险`** | 继承 LangGraph 的 reducer 坑，且规划/文件类工具可能引入额外状态 key |
| L2 | **`框架特有风险`** | 子 agent 的预算是否需要显式传入 |
| L3 | **`框架特有风险`** | 递归委派深度是否受限 |
| L4 | `框架已保证` | 人工确认机制内建 |

### deepagents 自省

```python
import inspect
import deepagents
print("deepagents:", getattr(deepagents, "__version__", "?"))
# 列出真实导出名 —— 不要凭记忆写 API 名
print("exports:", [a for a in sorted(dir(deepagents)) if not a.startswith('_')])
try:
    from deepagents import create_deep_agent
    print("create_deep_agent:", inspect.signature(create_deep_agent))
except Exception as e:
    print("create_deep_agent introspect failed:", e)
```

---

## Google ADK（google-adk）

> **⚠ 版本分叉提示（`[官方文档确认]` adk.dev/agents/workflow-agents/loop-agents/）**：`LoopAgent` 属于「template workflow」，官方标注 Supported in ADK **Python v0.1.0**，并明确说明「Starting in ADK 2.0 for Python and Go, templated workflows have been superseded by more flexible workflow structures, including graph-based workflows and dynamic workflows」。审计前**先确认项目用的是 template workflow 还是 2.0 的 graph workflow**——两套 API 不同，下面这张表描述的是 template workflow 一代。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证` | `LoopAgent` 有 `max_iterations` 参数 |
| M2 | `框架已保证` | 上限在 `LoopAgent` 的迭代内检查 |
| M3 | **`框架特有风险`** | `max_iterations` 数的是 **iteration（一轮 = 顺序跑完所有 sub_agents）**，所以换算比是 **1:N（N = 子 agent 数）**，不是 1:1。文档举例 `LoopAgent(sub_agents=[WriterAgent, CriticAgent], max_iterations=5)` 明确「The loop would run at most five times」。真实 LLM 调用数 ≈ `max_iterations × 子 agent 数 × 每个子 agent 内的 LLM 调用数`。**⚠ 早期版本写「换算比约 1:1，ADK 相对 LangGraph 最省事」是错的**——ADK 在这里比 LangGraph **更**不直观，因为一次 iteration 里就藏了多个子 agent。`[官方文档确认]` |
| M4 | **`框架特有风险`**（反向） | 超限是**正常终止**，不抛异常。你如果只写了 try/except 捕获异常，会**完全感知不到被截断**——这是 ADK 最容易踩的坑，和 LangGraph 行为相反 |
| M5 | `框架已保证` | 工具是声明式注册，未注册工具名不会被调用 |
| M6 | `框架已保证` | `FunctionTool` 从 Python 类型注解推导 schema，**类型不匹配是声明期错误**——M6 在结构上已被保证，检查结论是「无需自加」 |
| M7 | `默认不安全需自加` | 框架层不校验流式 tool call 的完整性 |
| M8 | `框架已保证` | 工具有确认回调机制 |
| M9 | `框架已保证` | 状态在 session/MemoryService 里，不在 reducer 里 |
| M11 | `框架已保证`（有处理机制） | 有 LLM 请求预处理钩子；回调类名为 `CallbackContext`（`from google.adk.agents.callback_context import CallbackContext`），工具侧用 `ToolContext`（`from google.adk.tools.tool_context import ToolContext`）。可在重试前改请求。`[官方文档确认]`（类名取自官方示例代码 import） |
| M13 | `框架已保证`（有确认机制） | 工具确认钩子可拦截敏感操作 |
| L2 | `默认不安全需自加` | `LoopAgent` 的上限是否下传到内部子 agent 需确认。官方对子 agent 主动终止给的机制是 `tool_context.actions.escalate = True`（子 agent 内自行信号退出），说明**终止是逐 agent 的，不由 LoopAgent 统一管**——这是一个支持「上限不下传」判断的间接证据 |
| L5 | `框架已保证` | Runner 的 session 机制内建持久化 |
| L7 | `框架已保证` | 有工具确认回调可路由到人工 |

### ADK 自省

```python
import inspect
from google.adk.agents import LoopAgent, LlmAgent
print("LoopAgent:", inspect.signature(LoopAgent.__init__))
print("LlmAgent:", inspect.signature(LlmAgent.__init__))
# 确认 max_iterations 默认值，以及是否有 while_condition
```

---

## Crawl4AI（crawl4ai）

> **⚠ 这是爬虫框架，不是工具调用 agent 框架。** M 层的语义整体偏移：M2/M5/M6/M7 讨论的「工具调用」在 Crawl4AI 里不存在。审计时要显式标注 `不适用` 并说明为什么，**不要硬套**。
>
> 证据等级 `[源码确认]`（unclecode/crawl4ai 源码 + README，2026-09-07 核验）。
>
> **核验推翻了一处前提**：早期版本把 Crawl4AI 的 agentic 部分当成「命名较新的待确认功能」，实际核验下来 **Crawl4AI 目前没有 agentic 策略**——它只有 BFS 深度爬取（默认最多 10 页）和内容抽取策略（`JsonCssExtractionStrategy` / `LLMExtractionStrategy`）。相关条目已从 `[待确认]` 改为 `不适用`。入口是 `AsyncWebCrawler` + `BrowserConfig` + `CrawlerRunConfig`，配 `CacheMode` / `LLMConfig`；内容过滤有 `PruningContentFilter` / `BM25ContentFilter`。所以本表按「爬虫 + 抽取」的实际形态写，不再预设存在 agent loop。
>
> **因此这份表的主要价值不在 M 层，而在 M13 与 L 层**：爬取内容直接进 prompt 是最高危的注入入口。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证` | BFS 深度爬取有页数上限（默认最多 10 页），另有超时配置 |
| M3 | **`框架特有风险`** | 上限数是**页面访问次数**，连「轮」都不是。要按「页面数 × 每页 LLM 调用次数」重算真实预算。抽取策略（`LLMExtractionStrategy`）对每页发起的 LLM 调用数是主要放大因子 |
| M4 | `默认不安全需自加` | 爬取失败与超时的区分：`CrawlerRunConfig` 返回的结果里要自己判定「是超时、是拒绝、还是页面本身没内容」 |
| M5/M6/M7 | `不适用` | Crawl4AI 没有工具调用抽象；它不是 agent 框架 |
| M8 | `框架已保证`（部分） | 有重试机制，但**重试是否换代理/换 User-Agent**决定它是否有效 |
| M9 | `框架已保证` | 状态在缓存/checkpoint 里，不是 reducer |
| M11 | **`框架特有风险`** | 限速/代理池是**爬虫特有的可重试性问题**：被封禁时的重试不改变结果，只烧配额 |
| M12 | `框架已保证`（部分） | `JsonCssExtractionStrategy` / `LLMExtractionStrategy` 支持结构化抽取 |
| M13 | **`框架特有风险`** | **爬取内容直接进 prompt = 最高危的 prompt injection 入口**。这是 Crawl4AI 用户最容易忽视的一项，因为它不在「工具调用」的检查清单里。缓解手段是内容过滤（`PruningContentFilter` / `BM25ContentFilter`），但**过滤是为了相关性，不是为了安全**——不要因为配了 filter 就判定这项通过 |
| L5 | `框架已保证` | `CacheMode` 支持缓存与续爬 |
| L9 | `默认不安全需自加` | 多页抽取结果的一致性/冲突检测不自带 |

### Crawl4AI 自省

```python
import inspect
import crawl4ai
print("crawl4ai:", getattr(crawl4ai, "__version__", "?"))
# 确认入口与配置项的真实签名
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    print("BrowserConfig:", inspect.signature(BrowserConfig))
    print("CrawlerRunConfig:", inspect.signature(CrawlerRunConfig))
except Exception as e:
    print("config introspect failed:", e)
# 确认抽取策略与内容过滤器（这些决定 M12/M13 的结论）
try:
    from crawl4ai.extraction_strategy import (
        JsonCssExtractionStrategy, LLMExtractionStrategy)
    from crawl4ai.content_filtering import PruningContentFilter, BM25ContentFilter
    print("extraction/filter ok")
except Exception as e:
    print("strategy introspect failed:", e)
```

---

## OpenAI Agents SDK（agents）

> 证据等级 `[官方文档确认]`（openai.github.io/openai-agents-python/，2026-09-07 核验）。参数名已核实，仍应自省核对本机版本。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证`（多口径） | `max_turns` / `max_tool_calls` / `max_execution_time` 三个口径**独立生效、独立触发**。这是它和 LangGraph/ADK 最大的结构差异：一个框架给了三个正交的 limiter |
| M2 | `框架已保证` | 检查点在每个 turn 之间执行，不在入口一次性检查 |
| M3 | **`框架特有风险`** | `max_turns` 数 turn，`max_tool_calls` 数工具调用——**一个 turn 里可以有多个工具调用，比例不固定**。不能把 turns 当 tool calls 换算；按 `max_tool_calls / max_turns` 的期望比值估算真实预算 |
| M4 | `框架已保证`（**硬抛异常，不是静默**） | 超限抛 **`MaxTurnsExceeded`**，不是正常结束。可用 `error_handlers={"max_turns": handler}` 接管，改成受控降级（返回部分结果、路由到别的 agent）。**⚠ 早期版本把这一格写成「需核实硬抛还是正常结束」——结论是确定的硬抛**，所以只写 try/except 的项目**会**感知到截断；风险反转为：`max_turns=None` 等于**关掉上限**（`None` 是官方给出的禁用方式），配置漂移时静默失效 |
| M5 | **`框架特有风险`**（可配置，默认报错） | 工具需显式注册；LLM 返回未注册工具名时，**默认抛 `ModelBehaviorError`**，不是静默回传 observation。可用 `tool_not_found_behavior` 改为回传错误消息让模型自愈。**⚠ 早期版本写「未注册工具名不会被执行 [待确认: 拒绝行为是报错还是回传]」——现在定论：默认报错** |
| M6 | **`框架特有风险`** | 工具函数的参数校验强度取决于是否用类型注解/JSON schema 定义。**用 `tool(func)` 包装普通函数时 schema 是从注解推导的，没有注解就是无校验**——这是最常见的 M6 漏点 |
| M7 | `默认不安全需自加` | 流式 tool call 完整性不在框架层 |
| M8 | **`框架特有风险`**（可配置，有默认） | 工具异常默认**进入消息流回传给模型**（由 `default_tool_error_function` 决定），不终止 run；可换成抛异常终止（`failure_error_function`），或用 `tool_use_behavior` 控制。审计重点：默认是「让模型自己看着办」，**模型可能把工具失败当成「任务已完成」** |
| M9 | **`框架特有风险`**（时序） | agent 级 guardrail 默认 `run_in_parallel=True`，**与主流程同时跑**——结果只影响最终输出判定，中间状态早已推进，**副作用类工具可能在 guardrail 判定前就已执行完**。要真正前置拦截须 `run_in_parallel=False` |
| M10 | `默认不安全需自加` | 流式完整性检查不在框架层 |
| M11 | `框架已保证`（有模型重试） | 模型调用层有内建重试（`ModelSettings` / `ModelRequest` 相关配置）；但**不区分可重试/不可重试错误**的自定义重试仍需自加 |
| M12 | `框架已保证` | structured output 是一等公民（`output_type` / `output_schema`）。校验失败可用 `error_handlers={"invalid_final_output": handler}` 接管 |
| M13 | **`框架已保证`（但两类 guardrail 语义不同）** | **工具 guardrail 才是真正的执行前闸门**：`tool(..., guardrails=...)` 在工具执行**之前**判定，不通过则工具**不执行**（抛 `ToolGuardrailTripwireTriggered` 类异常）。agent 的 input guardrail 不是（见 M9）。另有 `ToolExecutionConfig(pre_approval_tool_input_guardrails=True)` 可把 agent 级 input guardrail 提到工具调用前执行。触发类：`InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered` / `ToolGuardrailTripwireTriggered` / `ToolGuardrailFunctionError`。可经 `error_handlers` 接管。工具权限分级仍无内建，需自加 |
| M14 | `框架已保证`（可配置） | 模型参数可透传 seed |
| L2 | **`框架特有风险`**（但比早期版本认为的**好**） | **`max_turns` 是 run 全局的**：handoff 是「rerun the loop with a new agent」，不是新起一个 run，所以被委派 agent **共享**同一个 turn 预算——预算下传在这条路径上是成立的。**⚠ 早期版本写「不自动继承的可能性较高」是错的**。真正的宏观漏洞在别处：① `max_turns=None` 关掉的预算不会因为你加了 handoff 而回来；② `max_tool_calls` 与 `max_turns` 是独立预算，只调一个留着一个开着的口子；③ 若你用「自己重开一个 run」实现升级（而不是 handoff），那才是真正的预算重置 |
| L3 | **`框架特有风险`** | handoff 链深度：A→B→C→A 的循环委派没有内建深度上限，只能靠共享的 `max_turns` 兜底（见 L2）。也就是深度终止**依赖预算而非结构**——预算放宽则深度失控。`[待确认]`：是否存在独立的 handoff 深度计数器（未能在文档中定位） |
| L7 | `框架已保证`（有 handoff 抽象） | 升级/交接是一等公民，但「试到第几次做什么」仍需自定 |

### OpenAI Agents SDK 自省

```python
import inspect
import agents
print("agents:", getattr(agents, "__version__", "?"))
print("exports:", [a for a in sorted(dir(agents)) if not a.startswith('_')])
try:
    from agents import Agent, Runner, function_tool, tool
    print("Agent:", inspect.signature(Agent))
    print("Runner.run:", inspect.signature(Runner.run))
    print("function_tool:", inspect.signature(function_tool))
    print("tool:", inspect.signature(tool))
except Exception as e:
    print("Agent introspect failed:", e)
# 关键：确认 error_handlers 支持哪些 key、max_turns=None 是否是禁用方式
try:
    from agents import RunErrorHandlers, error_handlers
    print("RunErrorHandlers:", inspect.signature(RunErrorHandlers))
    print("error_handlers:", inspect.signature(error_handlers))
except Exception as e:
    print("error_handlers introspect failed:", e)
# 关键：确认 guardrail 异常类与触发时序
try:
    from agents import GuardrailFunctionError, InputGuardrailTripwireTriggered, \
        OutputGuardrailTripwireTriggered, ToolGuardrailTripwireTriggered, \
        ToolGuardrailFunctionError
    print("guardrail exceptions ok")
except Exception as e:
    print("guardrail exceptions introspect failed:", e)
```

**核实清单**（这三项已核实，剩下的仍要按本机版本核）：
1. `max_turns` 超限抛 `MaxTurnsExceeded`，`max_turns=None` 禁用上限。
2. handoff 共享 run 级 `max_turns` 预算（见 L2）。
3. guardrail 时序：agent 级 input guardrail 默认 `run_in_parallel=True`；工具 guardrail 是执行前闸门。
4. **仍未核实**：handoff 链是否有独立深度计数器（L3）。

---

## AutoGen / AG2（autogen-agentchat）

> **⚠ 本表未能核验，证据等级 `[待确认]`（2026-09-07）**。核验尝试：`microsoft.github.io/autogen/stable/sitemap.xml` 返回 404，文档站结构已重构；`ag2.ai` 从本网络不可达；HuggingFace 及其镜像站均超时。另发现 AG2 主分支已把 GroupChat 迁到 `network` 抽象（`website/docs/user-guide/network/termination.mdx`、`migration_from_group_chat.mdx`）。**所以下面这张表不能直接采信**——先确认项目用的是 AutoGen 0.4 的 GroupChat、AG2 0.8 的 GroupChat，还是 AG2 主分支的 network，三代 API 与终止语义都不同。参数名全部待自省确认。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证`（有上限） | GroupChat 类编排有回合上限 `[待确认: max_round 参数名]` |
| M2 | `框架已保证` | 回合数在每轮调度前检查 |
| M3 | **`框架特有风险`**（高） | `max_round` 数的是 **speaker turn（谁发言一次）**，不是「单个 agent 的轮次」，也不是 LLM 调用次数。一轮 round 里只有被选中的 agent 调一次 LLM → **实际 LLM 调用数 ≈ max_round，而不是 round × agent 数**。这是 AutoGen 最容易算错预算的地方，换算比接近 1:1 但语义和 ADK 的 iteration 不同 |
| M4 | **`框架特有风险`** | 超限后是**正常结束并返回当前状态**，不是抛异常。与 ADK 同类的「反向风险」——只写 try/except 感知不到被截断 |
| M5 | `框架已保证`（部分） | 工具按 agent 注册，agent 间默认不共享工具白名单。但**注册在哪个 agent 上直接决定它可见范围**，注册错 agent = 权限泄漏 |
| M6 | **`框架特有风险`** | 参数校验由 `@tool` / `FunctionTool` 从注解推导；注解不全则无校验。且嵌套 chat 中工具传递行为需核实 |
| M7 | `默认不安全需自加` | 流式 tool call 完整性不在框架层 |
| M8 | `默认不安全需自加` | 工具失败的处理需自定；多 agent 场景下失败可能被下一个 agent 当成「上一个 agent 的结论」继续传播 |
| M9 | **`框架特有风险`** | **对话历史在 agent 间共享**。历史污染是主要风险：一个 agent 的输出会成为后续所有 agent 的上下文，无隔离。M9 的「历史污染」项在该框架是主要故障形态 |
| M10 | `默认不安全需自加` | 流式完整性不在框架层 |
| M11 | `默认不安全需自加` | 框架不内建重试分类 |
| M12 | `框架已保证`（有结构化输出工具） | 结构化输出助手存在 `[待确认: 工具名]` |
| M13 | **`框架特有风险`** | 共享对话历史 + 多 agent = **agent 间互相注入的通道**。一个 agent 的输出会作为「同伴发言」进入另一个 agent 的上下文 |
| M14 | `框架已保证`（可配置） | 模型客户端参数可透传 |
| L2 | **`框架特有风险`**（高） | **嵌套 chat 的预算下传是该框架最普遍的漏洞**：外层 GroupChat 的上限不会自动约束内层嵌套的 chat。内层无上限 = 外层白设 |
| L3 | **`框架特有风险`** | 嵌套 chat 递归深度无内建上限 `[待确认]` |
| L9 | **`框架特有风险`** | 多 agent 给出矛盾结论时框架**不做冲突检测**，外层直接拼接发言历史 |

### AutoGen / AG2 自省

**这一步必须先做，而且要先判定「哪一代」，上面的表才能对着读。**

```python
import inspect
import importlib.util

# 先判定装了哪一代 —— 三代的 loop 语义不同，表不能混着看
for mod in ("autogen_agentchat", "autogen", "ag2"):
    spec = importlib.util.find_spec(mod)
    if spec:
        m = __import__(mod)
        print(mod, "FOUND", getattr(m, "__version__", "?"))

try:
    import autogen_agentchat as ac
    print("autogen_agentchat exports:", [a for a in sorted(dir(ac)) if not a.startswith('_')])
    from autogen_agentchat.teams import RoundRobinGroupChat
    print("RoundRobinGroupChat:", inspect.signature(RoundRobinGroupChat.__init__))
except Exception as e:
    print("autogen introspect failed:", e)

# AG2 主分支可能已迁到 network 抽象，而不是 GroupChat
try:
    import ag2
    print("ag2 exports:", [a for a in sorted(dir(ag2)) if not a.startswith('_')])
except Exception as e:
    print("ag2 introspect failed:", e)
```

**核实清单（本表全部条目待这一轮核实后才能采信）**：① 项目属于哪一代（AutoGen 0.4 / AG2 0.8 GroupChat / AG2 network）；② 上限的真实参数名与计数单位；③ 超限行为是抛异常还是正常结束；④ 嵌套 chat 的上限是否继承宿主；⑤ 对话历史在 agent 间是否隔离。

---

## LlamaIndex（llama-index — Workflows / AgentWorkflow）

> **⚠ 本表在 2026-09-07 核验后被大幅下调，M1/M3 结论从「框架已保证」改为 `默认不安全需自加`。** 证据等级 `[源码确认]`（llamaindex-ai/llama_index 源码）+ `[官方文档确认]`（developers.llamaindex.ai）。
>
> 核验拿到的三条硬事实（`[源码确认]`）：
> 1. **`AgentWorkflow` 不在 `llama-index-core` 里**——早期版本的自省片段写的 `from llama_index.core.agent.workflow import AgentWorkflow` 是错的，那个 import 会失败。
> 2. **`BaseWorkflowAgent` 与 `ReActAgent` 都没有 `max_steps` 字段**；core 的 `workflow/workflow.py` **完全没有任何步数上限**。
> 3. 状态模型不是 `AgentState` / `agentstate.py`，而是 **`AgentContext` Protocol + `StateStore`**。
>
> **结论：LlamaIndex 的 M1（硬预算）与 M3（计数单位）早期版本写错了。** 不是「框架给个参数、你调小一点」，而是**框架根本不提供**，必须自建。这是本表里和直觉相反最严重的一处。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | **`默认不安全需自加`** | **`BaseWorkflowAgent` / `ReActAgent` 没有 `max_steps`，core 的 `workflow/workflow.py` 没有任何步数上限。** 想限轮次得在 workflow 的 step 处理函数里自己维护计数并主动 `StopEvent`/结束。**⚠ 早期版本写「`max_steps` 控制步骤数 [待确认: 参数名与位置]」是错的**——不存在这个参数 |
| M2 | `默认不安全需自加` | 同 M1：没有内建计数器，检查点自然也没有 |
| M3 | **`框架特有风险`** | Workflow 的事件循环本身没有步数概念，所以「换算比」这个问题**不成立**——你得先自己定义「一轮」是什么，才有换算比可谈。事件类型是 `AgentInput` / `AgentSetup` / `AgentStream` / `AgentOutput` / `ToolCall` / `ToolCallResult`，计数要按哪个事件算自己定 |
| M4 | `默认不安全需自加` | 无内建超限机制，行为取决于你怎么写自己的计数器 |
| M5 | `框架已保证`（部分） | 工具显式注册；跨 workflow step 的工具可见范围需确认 |
| M6 | `框架已保证`（依赖定义方式） | 用 pydantic model 定义工具参数时校验强；用纯函数包装时弱 |
| M7 | `默认不安全需自加` | 流式 tool call 完整性不在框架层 |
| M8 | `默认不安全需自加` | 工具失败处理需自定 |
| M9 | **`框架特有风险`**（结构性不同） | **状态模型是 `AgentContext` Protocol + `StateStore`，不是 reducer**。这与 LangGraph 的 M9 结论方向相反：不存在「reducer 覆盖语义 + 并发分支写同一 key = 静默丢状态」这一坑，改为「context 是否被正确裁剪、StateStore 的读写是否一致」。审计时 M9 的检查方法要换：查 context 裁剪策略和 StateStore 用法，不要查 reducer |
| M10 | `默认不安全需自加` | 流式完整性不在框架层 |
| M11 | `默认不安全需自加` | 框架不内建重试分类 |
| M12 | `框架已保证` | 结构化输出是一等公民 |
| M13 | **`框架特有风险`** | **RAG 检索内容直接进 context = prompt injection 主入口**。这是 LlamaIndex 用户最该警惕的一项，因为它默认工作流就是「检索→拼 context」 |
| M14 | `框架已保证`（可配置） | 模型参数可透传 |
| L2 | `默认不安全需自加` | 既然根上没有上限（M1），「下传」无从谈起——多 agent workflow 的预算要全自建 |
| L5 | `框架已保证`（有状态持久化） | `StateStore` 提供状态持久化 |
| L9 | `默认不安全需自加` | 多 agent 结论冲突检测不自带 |

### LlamaIndex 自省

**`AgentWorkflow` 的真实 import 路径不确定，先发现再自省**——早期版本的 `llama_index.core.agent.workflow` 路径已确认错误，别照抄。

```python
import inspect
import pkgutil
import llama_index
print("llama_index:", getattr(llama_index, "__version__", "?"))

# 1. 先找出 AgentWorkflow 到底装在哪个包里（不在 llama-index-core）
for m in pkgutil.iter_modules():
    if "llama_index" in m.name or "llama-index" in m.name:
        print("installed:", m.name)

# 2. 逐一尝试候选路径，成功者为准
for path in ("llama_index.agents", "llama_index.core.workflow",
             "llama_index.core.agent", "llama_index.core.agent.workflow"):
    try:
        mod = __import__(path, fromlist=["*"])
        have = [a for a in dir(mod)
                if "Agent" in a or "Workflow" in a or "Context" in a]
        print(path, "->", have)
    except Exception as e:
        print(path, "-> FAIL:", type(e).__name__)

# 3. 拿到 AgentWorkflow 后确认它和 base agent 上有没有步数参数
try:
    from llama_index.core.agent import BaseWorkflowAgent, ReActAgent
    for cls in (BaseWorkflowAgent, ReActAgent):
        try:
            fields = getattr(cls, "__dataclass_fields__", {})
            stepish = {k: str(v.type) for k, v in fields.items()
                       if any(w in k.lower() for w in ("step", "max", "limit", "turn", "iter"))}
            print(cls.__name__, "step-ish fields:", stepish or "NONE")
        except Exception as e:
            print(cls.__name__, "introspect failed:", e)
except Exception as e:
    print("base agent introspect failed:", e)
```

**核实清单**：① `AgentWorkflow` 的真实 import 路径与所在包（决定这张表能否直接套用）；② 是否存在任何步数上限（已核实：core 里没有，但项目可能装了扩展包）；③ `AgentContext` / `StateStore` 的裁剪策略与默认 token 阈值；④ 子 agent 预算如何自建。

---

## smolagents（smolagents — HuggingFace）

> 证据等级 `[源码确认]`（huggingface/smolagents 源码，2026-09-07 核验；因 `huggingface.co` 与 `hf-mirror.com` 从本网络不可达，改经 `raw.githubusercontent.com` 直接读上游源码——这比读渲染后的文档证据更强）。
>
> 核验拿到的硬事实：`agents.py:300` `max_steps: int = 20`（**默认 20**）；超限走 `_handle_max_steps_reached`（`agents.py:606-637`），它会**再调一次 LLM** 走 `provide_final_answer` 合成最终答案，给结果挂 `AgentMaxStepsError`，然后**正常返回**、`state="max_steps_error"`。`LocalPythonExecutor` 官方文档**明确声明它不是安全边界**、限制可被绕过；真沙箱是 E2B / Blaxel / Modal / Docker executor。`output_type` 在 `agent_types.py`。
>
> 版本提示：`1.22.0` 起 `messages` 属性弃用，改用 `steps`。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `框架已保证` | `max_steps` 控制步数，**默认 20**（`agents.py:300`）。不是「不设就没上限」，是「默认给了 20，你可能没意识到 20 对你不够」 |
| M2 | `框架已保证` | 步数在每步后检查 |
| M3 | **`框架特有风险`**（低风险） | `max_steps` 语义接近 turn，换算比约 1:1。这是该框架相对 LangGraph 最省事的一项 |
| M4 | **`框架特有风险`**（高，行为反直觉） | **超限不是静默停止，也不是抛异常——而是再调一次 LLM 合成一个「看起来完整」的最终答案，然后正常返回，`state="max_steps_error"`**（`agents.py:606-637`）。三层风险：① 上层只写 try/except **完全感知不到**（同 ADK）；② 输出**看起来是正常完成的**，不报错、不带部分标记；③ 这次「收尾 LLM 调用」本身就是预算外消耗，可能把上下文撑爆或产出幻觉式总结。**⚠ 早期版本写「静默停止」是错的**——比静默停止更危险：静默停止给的是半成品，这里给的是「一个可能编造的完成品」。必须显式检查 `.state == "max_steps_error"`，**不要只看 `.output`** |
| M5 | **`框架特有风险`**（高） | **code agent 默认执行的是模型生成的 Python 代码，在 `exec` 里跑**，不走工具注册表。M5「执行前过注册表强校验」在这类 agent 里**结构性不成立**——检查方法必须换成「沙箱是否真隔离」，而不是「注册表是否完整」 |
| M6 | `不适用` | code agent 没有 schema 参数校验这一层；工具 agent 才有 |
| M7 | `不适用` | 无工具调用抽象（code agent） |
| M8 | **`框架特有风险`** | 代码执行失败是异常形态，但**是否重试、重试几次**需自定；生成代码里的异常可能被模型当成「正常输出」解读 |
| M9 | **`框架特有风险`** | 记忆由 `MemoryManager` 管理，代码 agent 的 memory 是**执行结果文本的堆积**，无结构化状态模型。长任务下 context 膨胀是主要风险 |
| M10 | `默认不安全需自加` | 流式完整性不在框架层 |
| M11 | `默认不安全需自加` | 框架不内建重试分类 |
| M12 | `框架已保证`（有限） | 有 `output_type` 约束输出形态（`agent_types.py`） |
| M13 | **`框架特有风险`**（最高） | **code agent + 沙箱配置不当 = 任意代码执行**。这是该框架唯一的一等风险，比所有其他 M/L 项都更该优先查。`LocalPythonExecutor` 官方文档**明确写着它不是安全边界、限制可被绕过**——所以「用了 `LocalPythonExecutor` 就安全了」是错的。真沙箱：E2B / Blaxel / Modal / Docker executor。检查点：沙箱是否真隔离、网络是否关闭、执行超时是否设置 |
| M14 | `框架已保证`（可配置） | 模型参数可透传 |
| L1 | `框架已保证` | 单 agent，无外层编排 |
| L2 | `默认不安全需自加` | 无内建委派；子 agent 场景需自定预算 |
| L5 | `默认不安全需自加` | 无内建检查点 |
| L7 | `默认不安全需自加` | 失败收敛需自定 |

### smolagents 自省

```python
import inspect
import smolagents
print("smolagents:", getattr(smolagents, "__version__", "?"))
print("exports:", [a for a in sorted(dir(smolagents)) if not a.startswith('_')])
try:
    from smolagents import CodeAgent, ToolCallingAgent
    print("CodeAgent:", inspect.signature(CodeAgent))
    print("ToolCallingAgent:", inspect.signature(ToolCallingAgent))
    # 关键：确认 max_steps 默认值与超限时的返回 state 值
    f = CodeAgent.__dataclass_fields__ if hasattr(CodeAgent, "__dataclass_fields__") else {}
    print("max_steps default:", getattr(f.get("max_steps"), "default", "?"))
except Exception as e:
    print("agent introspect failed:", e)
# 关键：确认当前用的是不是 LocalPythonExecutor（官方声明它不是安全边界）
try:
    from smolagents import LocalPythonExecutor
    print("LocalPythonExecutor in use? check executor= arg")
except Exception as e:
    print("executor introspect failed:", e)
```

**核实清单**：① `max_steps` 默认值（已核实 20，仍应核对本机版本）；② 超限返回的 `state` 值，以及上层有没有检查它（决定 M4 结论）；③ code agent 的 executor 是本地还是真沙箱（决定 M5/M13 结论）；④ 是否支持 `ToolCallingAgent` 及其工具校验强度。

---

## 自研 loop（无框架）

**这是主路径。** 真实项目里裸 `while` + 直接调 openai/anthropic client 的比例通常高于用框架的项目，且没有任何框架兜底。

| ID | 结论 | 说明 |
|---|---|---|
| M1-M14 | 全部 `默认不安全需自加` | 没有框架意味着**没有任何一项是自动满足的**。逐项检查，不要跳项 |
| 额外 | `框架特有风险` | 自研 loop 最常见的三个坑：① 上限只在循环外校验一次 ② 重试循环没有 backoff ③ 异常被 `except Exception: pass` 吞掉导致 agent 以为成功 |

### 自研 loop 识别信号

```python
# 源码里同时出现以下两类，基本就是自研 loop
IMPORT_LLM = ["openai", "anthropic", "openai.agents", "google.generativeai", "genai",
              "mistralai", "cohere", "ollama", "litellm"]
LOOP_PATTERNS = ["while True", "while not", "for attempt in range", "max_retries", "retry"]
```

### 案例：自研 BSP 波次引擎（生产形态，`[源码确认]` 2026-09-07）

第一个逐行核验过的**生产形态**自研 loop。形态是「Plan（静态蓝图）→ Run（一次执行）→ Scheduler 驱动 BSP 波次」：节点从不写共享状态、只返回 `Outcome`，由屏障统一折叠。**这个形态制造了几类框架级陷阱，比裸 while 循环隐蔽得多。**

结论列沿用填表纪律的四选一词汇；半对半错的情形按「缺的那一半是风险」判 `框架特有风险`，细节写在说明列。

| ID | 结论 | 说明 |
|---|---|---|
| M1 | `默认不安全需自加` | 只有 `max_waves=64` 一项上限。`llm_calls` / `tool_calls` / `tokens` 都在计数（`body.py:167-168,177`）也进了快照（`run.py:265`），但**没有任何代码把它们当上限读**，唯一被读的是 `metrics["waves"]` → 64 波内收敛的 Run 可以烧掉任意多的模型调用 |
| M3 | `框架特有风险` | **换算比不是常数，可以任意大**。1 波 ≈ 1 次 LLM + M 个工具调用（ReAct）；≈ N 个 worker（plan-first，N 由模型输出决定）；`SubPlanBody`（`body.py:239-250`）把**一整个子 Run**（自身 64 波）塞进一个节点——父层数 1，子层跑 64 波。审计自研 loop 时问「一次循环迭代 = 多少次 LLM 调用」，答不上来就别判 `OK` |
| M9 | `框架已保证` | 声明式 channel + 显式 reducer + `allow_multi` 标志，未声明通道写入抛错并指名写入者（`run.py:224-243`）。**并发歧义在构造期就无法表达**，不是运行期抛异常——这是本案例的正向标杆，做法细节见 `references/loop-engineering-patterns.md` 模式 1/2 |
| L3 | `框架特有风险` | 深度有结构性守卫（`scheduler.py:83-91`），**广度无任何上限**：`add_instance`（`run.py:207-217`）不计数，一次 `Send` 一万副本 = 一万个节点同一波 ready。深度守卫存在 ≠ L3 通过 |
| L6 | `框架特有风险` | 幂等键**存在且被文档化**（`types.py:60-68`），但键是 `{run_id}:{node_id}:{attempt}`（`body.py:178-180`）——同一节点同一 attempt 内的多个工具调用**全部碰撞**（`react.py:81` 循环执行多个待处理调用），且 `dispatch`（`tools.py:117-139`）从不读这个键。错误的去重比没有去重更危险：操作**静默丢失**而非报错 |
| L8 | `框架特有风险` | 退避是纯指数（`graph.py:55-58`），**无 jitter、无最大延迟上限**；`retry_on` 默认 `(Exception,)` 会把程序 bug 也重试。策略机制好（谓词可替换），默认值糙 |
| X5 | `框架特有风险` | 枚举配置全做白名单校验、全 fail-closed（`scheduler.py:121-122`、`bus.py:103-104`、`graph.py:119-141`）；**数字配置零校验**——`concurrency=0` 信号量永久锁死且无异常无日志，`max_waves=-1` 完全无上限 |

**自研 loop 的第四、第五个常见坑**（补上方表格的三个）：

④ **幂等键存在但粒度太粗**——键用节点级/attempt 级而非操作级，同一波内多个操作碰撞；且无人强制读取，等于没有去重。

⑤ **门面层与内核层策略不一致**——声明式门面为未声明的 key 自动补 channel（`workflow.py:83-85`），内核层对同一行为是硬错误（`run.py:224-243`）。用户从门面层养成的「静默放宽」习惯会带进内核层。审计时判 `RISK`，不要因其中一层正确就判 `OK`。

另：**上层构造器漏传参数**。`Agent._scheduler()`（`agent.py:164-171`）构造 Scheduler 时没有传 `max_waves`，落回默认值——不是「配置错了」，是「这条路径根本没接到配置」。审计 L2 时除了问「有没有下传」，还要问「每一条构造路径都接上了吗」。

---

## 如何新增一个框架

复制以下模板到本文件末尾，填三样东西。

```markdown
## <框架名>（<包名>）

| ID | 结论 | 说明 |
|---|---|---|
| M1  | ? | ? |
| ... |   |   |

### <框架名> 自省

```python
import inspect
import <包名>
print("<包名>:", getattr(<包名>, "__version__", "?"))
print("exports:", [a for a in sorted(dir(<包名>)) if not a.startswith('_')])
```

### 已知语义陷阱（标注版本与证据等级）

- <陷阱>（`[源码确认]` src/path.py:行号 / `[官方文档确认]` URL / `[版本知识]` 基于 X 版本的已知行为 / `[待确认]` 核验尝试过但无定论）
```

**填表纪律**：
1. 每格四选一，不允许写「可能有」——那是结论缺失，不是结论。
2. 每格必须给证据来源，四档从高到低：`[源码确认]`（file:line）> `[官方文档确认]`（URL + 页面所述版本）> `[版本知识]`（未核验，标注版本）> `[待确认]`（核验过但无定论，不做断言）。若跑通了本机自省，可另加 `[自省实测]`。
3. 自省跑不通的条目一律 `[待确认]`，绝不写具体 API 名当断言。
4. 继承其他框架的，明确写「继承 X 的结论，除下列除外」，不要复制粘贴整表再改——继承关系变了就全表作废。
5. 核验过的条目就地写「**⚠ 早期版本写 X 是错的**」——保留错误记录比抹掉它更有价值，后来的维护者才知道这一格为什么被反复改动过。

---

## 核验记录（2026-09-07）

本轮核验按「优先读官方文档，文档不可达时读上游源码」的顺序执行。结果：**10 处早期版本的结论被推翻**，逐处已就地标注 `⚠ 早期版本写 X 是错的`。另有 2 处整张表的前提被推翻（LlamaIndex 的 M1/M2 从「框架已保证」整体下调为 `默认不安全需自加`；Crawl4AI 的 agentic 条目从 `[待确认]` 改为 `不适用`）。

### 已核验（结论已升级）

| 框架 | 证据来源 | 核验到的关键分歧点 |
|---|---|---|
| LangGraph | `[官方文档确认]` docs.langchain.com（`/llms.txt` 索引 + 逐页 Markdown） | M3 计数单位是 step/superstep；M9 并发写抛 `INVALID_CONCURRENT_GRAPH_UPDATE` 而非静默丢状态；M11 默认重试**区分**可重试错误；L7 `interrupt()` 恢复会重跑中断前代码；L2 子图 checkpointer 三种继承模式 |
| OpenAI Agents SDK | `[官方文档确认]` openai.github.io/openai-agents-python/ | M4 抛 `MaxTurnsExceeded`；M5 默认抛 `ModelBehaviorError`；M9 guardrail 默认 `run_in_parallel=True`；M13 工具 guardrail 是真正的执行前闸门；L2 `max_turns` 是 run 全局、handoff 共享预算 |
| Google ADK | `[官方文档确认]` adk.dev | M3 一轮 = 顺序跑完所有 sub_agents，换算比 1:N；M4 超限是正常终止不抛异常；M11 回调类名 `CallbackContext` / `ToolContext`；ADK 2.0 用 graph/dynamic workflow 取代 template workflow |
| smolagents | `[源码确认]` huggingface/smolagents | M1 `max_steps` 默认 20（`agents.py:300`）；M4 超限走 `provide_final_answer` 再调一次 LLM 后正常返回（`agents.py:606-637`）；M13 `LocalPythonExecutor` 官方声明不是安全边界；M12 `output_type`（`agent_types.py`） |
| LlamaIndex | `[源码确认]` llamaindex-ai/llama_index + `[官方文档确认]` developers.llamaindex.ai | `AgentWorkflow` 不在 `llama-index-core`；`BaseWorkflowAgent`/`ReActAgent` **无 `max_steps`**；core `workflow/workflow.py` **无任何步数上限**；状态是 `AgentContext` Protocol + `StateStore`。→ M1/M3 从「框架已保证」下调为 `默认不安全需自加` |
| Crawl4AI | `[源码确认]` unclecode/crawl4ai | **无 agentic 策略**（BFS 深度爬取默认最多 10 页 + 抽取策略）→ 原 agentic `[待确认]` 条目改为 `不适用` |

### 未核验（结论未升级，表中已标注）

- **AutoGen / AG2**：`microsoft.github.io/autogen/stable/sitemap.xml` 返回 404，文档站结构已重构；`ag2.ai` 从本网络不可达。另发现 AG2 主分支已把 GroupChat 迁到 `network` 抽象。该表整表维持 `[待确认]`，自省必须先判定「哪一代」。
- **deepagents**：未独立核验，仅继承 LangGraph 结论。
- **LangGraph 子图上限继承**（L2 的具体行为）：文档确认了「需要显式传递」与 checkpointer 三种模式，但「是否默认继承宿主的 recursion_limit」未拿到定论。
- **OpenAI Agents SDK handoff 深度计数器**（L3）：确认深度终止依赖共享 `max_turns`，但独立的 handoff 深度计数器未能定位。
- **LlamaIndex 是否存在扩展包提供步数上限**：只确认 core 里没有。

### 网络可达性（影响复现方式）

本轮从该网络可直接访问：`docs.langchain.com`、`openai.github.io`、`adk.dev`、`developers.llamaindex.ai`、`raw.githubusercontent.com`、`api.github.com`。

**不可达**：`huggingface.co`（连接超时）、`hf-mirror.com`（超时）、`www.modelscope.cn`（不可达）、`ag2.ai`（不可达）。

因此 smolagents 与 LlamaIndex 的证据走的是 `raw.githubusercontent.com` + `api.github.com` 直读上游源码——**这比读渲染后的文档站证据更强**（拿到了 file:line），但也意味着这些结论绑定的是核验当日的主分支，可能与你本机的发行版本不同。

> 方法学注记：LangGraph 的官方文档站提供 `/llms.txt` 索引与逐页 Markdown，纯文本、无 HTML 噪声，是本轮最快的核验路径。其他框架的文档站需要先用 `sitemap.xml` 探路才能定位正确路径（本轮因路径错误各踩了 3 次 404）。**审计时对无法定位路径的文档站，应降级为读源码而不是凭记忆补全。**
