# Loop Engineering 审计报告：自研 BSP 波次引擎（案例 1）

```
审计对象：自研 BSP 波次引擎（案例 1；本地快照 /tmp/paf/，gh api 抓取 100/100 文件，src/ 5012 行）
框架：自研 loop，无框架兜底　　审计时间：2026-09-07T17:50:00+08:00
结论：BLOCK
阻断级 1 项　缺口 11 项　风险 9 项　通过 6 项　待确认 3 项（合计 29 = M14 + L10 + X5，M/L/X 三层全覆盖）
分区校验：11 + 9 + 6 + 3 = 29 ✓　（阻断级是严重度叠加计数，M1 同时计入缺口，不是分区项）
```

判定规则：`blockers > 0` → `BLOCK`。唯一阻断项是 M1（硬预算熔断缺失）。

---

## 1. 框架识别

```
声明版本：pyproject.toml — dependencies = []（运行时零依赖；dev 仅 pytest/pytest-asyncio/ruff）
源码使用：无 langgraph / langchain / google.adk / deepagents / crawl4ai / agents / autogen / llama_index / smolagents 的 import
          唯一的模型 client 是 src/runtime/openai_lite.py，用 stdlib urllib 手写 POST
自省结果：不适用——本机未安装该库，也无外部框架可自省。证据全部来自本地源码 file:line
结论：自研 loop，无框架兜底（证据等级 [源码确认]）
```

依据：

- `src/runtime/openai_lite.py:77-132` — `OpenAICompatibleLlm` 用 `urllib.request.urlopen` 直发 `/chat/completions`，无第三方 SDK。
- `src/kernel/scheduler.py:173-247` — 主循环是手写的 BSP 波次循环（`drive()` 内 `while True`），不依赖任何编排框架。
- `src/runtime/react.py:39-105` — ReAct 由 3 个节点 + 2 条条件边 + 1 条回边拼出，不是框架内置的 ReAct。

**影响：M1–M14 / L1–L10 / X1–X5 全部需自查，无一项由框架自动满足。框架差异矩阵整体跳过（见第 3 节）。**

需要说明这个库的特殊性：它是**一个自研 agent 内核本身**，所以它是本技能不变式层的理想测试对象——每一项都要查。但同时它的 L 层（宏观 loop engineering）做法比 M 层（微观 agent loop）更值得吸取，见第 4 节。

---

## 2. 发现

### M 层（微观 Agent Loop）

#### M1 · 硬预算熔断　[GAP / blocker]

**现象**：整个代码库只有一个循环上限——波次数 `max_waves`。`llm_calls`、`tool_calls`、`tokens` 三个计数器都在正常递增并被写进快照，但**没有任何代码把它们当作上限读回来**。也就是说：没有 LLM 调用次数上限、没有工具调用次数上限、没有 token/成本上限、没有墙钟时间上限。

**证据**：
- `src/kernel/run.py:104` — `metrics = {"waves": 0, "llm_calls": 0, "tool_calls": 0}`，定义处。
- `src/kernel/body.py:167-168` — `self.run.metrics["llm_calls"] += 1` / `metrics["tokens"] += reply.tokens`，递增。
- `src/kernel/body.py:177` — `self.run.metrics["tool_calls"] += 1`，递增。
- `src/kernel/run.py:265` — `snapshot()` 把 `metrics` 存进检查点，快照保留。
- `src/kernel/scheduler.py:116-119` — 唯一的上限默认值：`max_waves=64, concurrency=8, max_depth=8, durability="sync"`。
- `src/kernel/scheduler.py:190-196` — 全库唯一的上限检查：
  ```python
  run.metrics["waves"] += 1
  if run.metrics["waves"] > self.max_waves:
      run.fail(f"exceeded max waves {self.max_waves}; suspected spin (check that a back-edge makes progress)")
      await self._emit(run, RUN_FAILED, {"reason": run.final_output})
      break
  ```
- 反向验证：`grep -rn "metrics\[" src/` 只有 `waves` 被 `if` 读取，`llm_calls` / `tool_calls` / `tokens` 仅出现在递增与快照语句里。

**后果**：一个在 64 波之内收敛的 Run 可以任意烧钱。最现实的场景是 ReAct 回边——`think → tools → think`，每圈至少 1 次 LLM 调用，通常 1 次 LLM + N 次工具调用。`max_waves=64` 意味着 64 圈、64+ 次 LLM 调用，且每圈工具调用数不受限（`react.py:81` 对全部 pending 调用逐个执行）。`SubPlanBody` 更糟：它在**一个节点内**递归跑完整个子 Run，子 Run 自己又拿一份 `max_waves=64`。父 Run 3 波内嵌一个烧完 64 波的子 Run，父计数只走 1。真实账单可以是声明上限的十几倍到几十倍，且**代码库没有任何机制能提前发现**——计数器都在，就是没人看。

顺带一提：`LlmReply.tokens` 只取 `usage.total_tokens`（`openai_lite.py:232`），没有区分输入/输出计费，但更关键的是它根本没有被当作上限用，所以这一条只是次要问题。

**建议**：把预算做成**多计数器联合熔断**，并在同一个位置检查（`scheduler.py:190` 旁边），超限行为复用已有的 `run.fail() + RUN_FAILED`：

```python
# scheduler.py，紧跟 waves 检查之后
for key, limit in (("llm_calls", self.max_llm_calls),
                   ("tool_calls", self.max_tool_calls),
                   ("tokens", self.max_tokens)):
    if limit is not None and run.metrics.get(key, 0) > limit:
        run.fail(f"exceeded {key} budget {limit}; actual {run.metrics.get(key, 0)}")
        await self._emit(run, RUN_FAILED, {"reason": run.final_output})
        break
```

配套两件事：
1. 子 Run 必须继承**剩余**预算而不是新的一份（见 L2）。
2. 加墙钟上限。`Run.start` 已经有 `started_at`，一行就能判：`if time.time() - run.started_at > self.max_seconds`。

测试锚点：`tests/test_graph_scheduler.py:123-127` 已有一个 `test_max_waves_guards_spin`（`Scheduler(max_waves=5)`），照它加 `test_max_llm_calls_guards_budget`，用 `ScriptedLlm` 给一个永不收敛的回边脚本。

---

#### M2 · 检查点位置　[RISK / minor]　⚠ 已升级，见 §8.1（GAP / major）

本节按 v1.2.0 判据写，判定为 RISK/minor。**v1.3.0 的正向判据重查后升级为 GAP/major**——原判只验证「检查点位置对不对」（答案：对），新判据问「每一条进入引擎的构造路径都接到配置了吗」（答案：否）。详见 §8.1，结论以 §8.1 为准。

**现象**：唯一的上限检查放在循环**内部**、每波执行一次——位置是对的。问题是它只管一个计数器，另外三个计数器（`llm_calls` / `tool_calls` / `tokens`）虽然也在同一个位置递增，却没有对应的 `if`。

**证据**：`src/kernel/scheduler.py:190-196` 是 `drive()` 主循环内、每波一次的位置；`src/kernel/body.py:167-168` 与 `body.py:177` 的递增发生在节点执行时，比屏障早。

**后果**：即使把 M1 补上，也要小心别把新检查放在节点内部——那样一个波里 8 个并发节点各调一次 LLM（`scheduler.py:199` 的 `asyncio.gather`），本波预算已经被超了但检查要等到下一波屏障才生效，等于每个波次超发一次并发宽度。

**建议**：所有预算检查统一放在 `scheduler.py:190` 的屏障位置（每波一次、检查上一波完整结果），并额外在 `body.llm_chat`（`body.py:166`）里做一次**调用前**预检，避免单波内超发。两处都要读同一个 `run.metrics`。

---

#### M3 · 换算比　[RISK / major]

**现象**：`max_waves` 数的是波，不是 LLM 调用。一个波内的实际工作量取决于本波就绪节点数，而这个数量不受任何约束。

**证据**：
- `src/kernel/scheduler.py:199` — `asyncio.gather(*tasks)`，本波就绪节点全部并发；`scheduler.py:265` 的信号量只限并发度，不限总量。
- `src/kernel/graph.py:204-219` — `ready()` 一次性返回本波全部就绪节点，包括 `Send` 动态实例化出的实例（`graph.py:211-212`：动态实例只要 pending 就立刻就绪）。
- `src/runtime/react.py:81` — `for call in ctx.shared["pending"]:` 在**一个节点内**遍历模型给出的**全部** tool_calls 并逐个执行。
- `src/kernel/body.py:239-250` — `SubPlanBody.run()` 在一个节点内递归跑完整个子 Run。
- `src/runtime/plan_first.py:55-64` — planner 一条消息产 N 个 `Send`，N 由模型输出决定，一个波内起 N 个 worker。

**换算比**：单线路径（纯 ReAct）约 1 波 ≈ 1 次 LLM 调用 + M 次工具调用，M 由模型单轮返回的 tool_calls 数量决定，无上界。plan-first 路径 1 波 ≈ N 个 worker，每个 worker 内部可能各跑一次 LLM。`SubPlanBody` 路径 1 波 ≈ 一整个子 Run（最多 64 波 × 各自的 LLM 调用）。**换算比不是常数，且可以任意大。**

**后果**：用 `max_waves=64` 当"轮数预算"是危险的。64 波 ≠ 64 次 LLM 调用，可能是 64 次，也可能是一千多次。反过来，一个真正在 20 波内无限递归 spawn 子 Run 的失控场景，父 Run 只看得到 20。

**建议**：把"波数"和"钱"解耦——M1 的修复（`max_llm_calls` / `max_tool_calls` / `max_tokens` 直接读计数器）同时把换算比问题消掉了：不再需要通过波数估算成本。保留 `max_waves` 作为防自旋保险，但注释里写清它不是成本上限。

---

#### M6 · 参数类型校验　[GAP / major]

**现象**：工具参数校验只检查"缺不缺必填"，**从不检查类型**，也**不拒绝多余字段**。schema 是推出来的，但推出来之后没有任何东西去强制它。

**证据**：
- `src/runtime/tools.py:34-47` — `infer_schema` 把 Python 注解映射成 JSON schema，未知注解降级成 `"string"`（第 43 行 `json_type = _PY_TO_JSON.get(p.annotation, "string")`），只描述不校验。
- `src/runtime/tools.py:117-126` — `dispatch()` 的唯一参数检查：
  ```python
  missing = [k for k in spec.parameters.get("required", []) if k not in call.arguments]
  if missing:
      return ToolResult.failure(f"missing required arguments: {missing}", call.call_id)
  ```
- `src/runtime/tools.py:155-163` — `_invoke()` 只从签名参数取字段，**模型传了多余字段被静默丢弃**，不报错、不告知模型。
- 全库反向验证：`grep -rn "json_type\|isinstance.*arguments\|schema.*valid" src/` 无类型校验逻辑。

**后果**：`def write_note(title: str, days: int)` 收到 `{"title": 3, "days": "next week"}`，会原样执行。具体场景：模型把 `days: 30` 写成 `"30"` 或把 list 参数写成字符串，工具内部 `days * 2` 之类的表达式要么算出错值（`"30" * 2 == "3030"`），要么抛 `TypeError`。第二条路径还算安全（异常被 `tools.py:138-139` 转成反馈回模型）；**第一条路径是静默的数据错误**，agent 会基于错误结果继续，用户看到的是"看起来很合理但数值是错的"输出。

更隐蔽的一处：MCP 工具直接透传外部 `inputSchema`（`src/runtime/mcp.py:130` `t.get("inputSchema", ...)`），schema 可能声明了 `type` / `enum` / `minimum` 等约束，内核一个都不执行。

**建议**：在 `dispatch()` 的 `missing` 检查之后加一段最小类型校验（stdlib 就能做，符合本库零依赖立场）：

```python
for name, spec in (spec.parameters.get("properties") or {}).items():
    got = call.arguments.get(name)
    if got is None or got == "":
        continue
    t = (spec or {}).get("type")
    if t == "integer" and not (isinstance(got, int) and not isinstance(got, bool)):
        return ToolResult.failure(f"argument '{name}' should be integer, got {type(got).__name__}", call.call_id)
    if t == "string" and not isinstance(got, str):
        return ToolResult.failure(f"argument '{name}' should be string, got {type(got).__name__}", call.call_id)
```

风格与本库一致：失败作为**反馈**回模型，而不是抛异常炸图（`tools.py:124-126` 的既有立场）。同时建议把"多余字段"也报出来——不执行、只告知，模型才有机会修正。

---

#### M7 · 工具调用 JSON 结构　[GAP / major]

**现象**：模型返回的工具调用参数如果是一段截断的 JSON，会被静默替换成空字典 `{}`，然后工具照常执行。

**证据**：
- `src/runtime/openai_lite.py:220-223`（非流式路径）：
  ```python
  try:
      args = json.loads(fn.get("arguments") or "{}")
  except json.JSONDecodeError:
      args = {}
  ```
- `src/runtime/openai_lite.py:227-230`（流式路径，SSE 分片拼装后）：
  ```python
  try:
      args = json.loads(slot["args"] or "{}")
  except json.JSONDecodeError:
      args = {}
  ```

**后果**：流式分片没传完就断流（网络抖动、`max_tokens` 用完、网关超时）时，`slot["args"]` 是不闭合的 JSON。这里吞掉异常返回 `{}`，于是模型"请求调用 `delete_issue(id=123)`"变成了"请求调用 `delete_issue()`"。如果这个工具恰好有默认参数，它**真的会去执行一次没有目标的删除**。如果工具没有默认参数，`_invoke` 抛 `TypeError: missing argument`（`tools.py:161`），异常在 `tools.py:138` 被转成反馈——这条路径反而是安全的，但前提是你依赖工具的签名，而不是依赖解析成功。

同一文件里就有一个正确的反例：空流会**大声失败**（`openai_lite.py:200-209`），并保留 3 条原始 SSE 分片作诊断。说明作者知道"不要假装"，只是这一处没做到。

**建议**：把 `except` 的分支从"静默降级"改成"带诊断的失败"，走 ToolResult 反馈通道而不是抛异常：

```python
try:
    args = json.loads(slot["args"] or "{}")
except json.JSONDecodeError:
    return LlmReply(
        text=f"[truncated tool call: {slot['name']}(…{len(slot['args'])} chars of unclosed JSON)]",
        tool_calls=[],
    )
```

或直接抛 `RuntimeError(f"tool call '{slot['name']}' arguments truncated: {slot['args'][:200]!r}")`，让节点的 retry 策略去处理。**关键是把"参数不全"变成显式事件**，让它出现在事件日志里，而不是变成一个合法的空调用。

---

#### M10 · 部分输出与流式完整性　[GAP / major]

**现象**：不检查响应是否被截断。模型输出撞到 `max_tokens` 上限时，半截的回答会被当作完整回答使用。

**证据**：`grep -rn "finish_reason\|stop_reason\|truncated" src/` — **零命中**。全库没有任何一处读 `choices[0].finish_reason`。`src/runtime/openai_lite.py:214-233` 的 `_reply_from()` 直接取 `msg.get("content")`，不看终结原因；流式路径 `openai_lite.py:154-175` 读到 `[DONE]` 就结束，也没有核对最后一个 chunk 的 `finish_reason`。

**后果**：`max_tokens=8192`（`openai_lite.py:100` 默认）用尽时，模型可能正在输出 tool_calls 的 arguments JSON——这个场景正好和 M7 叠加：截断的参数 → 静默 `{}` → 空调用。或者模型正在写最终答案，你拿到的是一段写到一半的结论，agent 把它当完成品交给用户，且**事件日志里没有任何标记**。在 reasoning 模型上更明显：注释自己就写了 reasoning 内容会占输出预算（`openai_lite.py:97-99`），预算被思考吃光时正文为空。

**建议**：在 `_reply_from()` 里读出 `finish_reason` 并带进 `LlmReply`：

```python
reason = raw["choices"][0].get("finish_reason") or ""
# LlmReply 增加 truncated: bool = False 字段
truncated = reason in ("length", "max_tokens")
```

然后 ReAct 的 `think` 节点（`react.py:62`）在 `reply.truncated` 时不把它当最终答案，而是走 `final` 之外的路径——要么报错，要么把"回答被截断，请继续"作为一条消息回给模型。至少应该把 `truncated=True` 写进事件日志，让用户能区分"模型说完了"和"预算用完了"。

---

#### M12 · 输出契约校验　[GAP / major]

**现象**：终端节点的输出原样透传，没有任何 schema 校验。

**证据**：`src/kernel/scheduler.py:378-385` 的 `_final_output()` 直接返回终端节点的输出；`src/runtime/react.py:93` 的 `final` 节点是 `lambda x, ctx: Outcome.ok(ctx.shared["answer"])`，把模型自由文本塞进 `answer` 通道就完事。`src/runtime/plan_first.py:67-69` 的 `default_synth` 直接 `return Outcome.ok(inputs)`。

**后果**：这个内核对外承诺的是 `Run.final_output` / `WorkflowResult.output`。下游调用方（`src/runtime/agent.py:159-162` 的 `as_task()` 把子 Agent 输出塞回父图的字符串里）拿到的是任意类型的对象——可能是字符串，可能是 list（`coerce_outcome` 允许 list 作为业务值），可能是 dict。父 Agent 的模型看到的是 `str(input or "")`（`agent.py:160`），如果子 Agent 返回了一个 list，`str()` 会把它变成 Python repr，模型读到的是 `['a', 'b']` 而不是可读文本。这个不会报错，只会让跨 agent 交接的语义悄悄劣化。

**建议**：本库的定位是教学内核，不一定要加 schema 机制，但至少应该在文档里明确"`final_output` 无契约保证"。如果要做，最小实现是在 `Node` 上加一个可选的 `output_schema`，在 `_final_output()`（`scheduler.py:378`）处校验，失败走 `run.fail()` 而不是静默通过。

---

#### M13 · 安全护栏　[RISK / major]

**现象**：有一个**真实存在**的权限边界——`side_effect: read|write` 分类，write 类工具在执行前过 `bus.check()` 审批门，且审批是 fail-closed。但边界只覆盖"直接调用工具"这条路，**委派工具绕过了它**。

**证据**：
- `src/runtime/tools.py:58` — `side_effect: str = "read"`。
- `src/runtime/tools.py:128-131` — write 检查：
  ```python
  if spec.side_effect == "write" and self.write_needs_approval and self.bus is not None:
      verdict = await self.bus.check(f"tool:{spec.name}", call=call, ctx=ctx)
      if not verdict.allowed:
          return ToolResult.failure(f"operation not approved: {verdict.reason}", call.call_id)
  ```
- `src/kernel/bus.py:136-148` — `check()` 是 fail-closed：checker 抛异常时返回 `BlockingResult(False, f"checker error: {exc}")`，**一个坏掉的审批器是否决票，永远不会变成放行票**。
- **绕过点 1**：`src/runtime/multiagent.py:89` — `register_agent_tool()` 把委派工具标成 `side_effect="read"`，而这个工具会递归 spawn 一个子 Run，子 Run 能触达子 agent 自己的全部工具（包括它的 write 工具）。
- **绕过点 2**：`src/runtime/mcp.py:78` — `load_mcp_tools()` 把**所有** MCP 工具硬编码成 `side_effect="read"`，无论外部 MCP server 声明的是什么。MCP 工具通常是真正有外部副作用的（发消息、写文件、调外部 API）。
- **绕过点 3**：无 prompt injection 处理。爬取/外部内容直接进 prompt——`src/runtime/context.py` 的压缩器只按长度处理，不做净化；`memory.recall()` 的结果直接拼进 system prompt（`react.py:50-52`）。
- **绕过点 4**：无 PII 处理，无输出泄露过滤。`grep -rni "pii\|injection\|sanitize\|redact" src/` 零命中。

**后果**：最严重的是绕过点 2。`write_needs_approval=True` 是 `ToolRegistry` 的默认值（`tools.py:64`），作者显然是想默认安全。但一个装了 MCP server 的用户看到的所有工具都会被标成 read，审批门完全不生效。一个能改 GitHub issue 的 MCP 工具，在用户以为需要审批的前提下被无条件执行了。委派绕过同理：supervisor 的一个"读"动作实际执行了 worker 的写操作，审计日志里看不到 write 门被触发。

**建议**（按优先级）：
1. `load_mcp_tools` 不要硬编码 `side_effect="read"`。MCP 工具无法可靠判断副作用，那就改成**默认 write**（fail-safe 方向），并支持显式声明：`load_mcp_tools(registry, server, read_tools={"fetch": ...})`。审批成本远低于误删数据的成本。
2. 委派工具标成 `side_effect="write"`，让审批门在委派时也生效。或者更准确的做法：审批门检查"这次调用可能触达的写工具集合"，而不是只看直接调用的那一个。
3. 把"外部内容进 prompt"显式当成一个待补的能力，在文档里标注。本库是教学型的，不一定要实现净化，但**至少要有一处注释指出这是已知缺口**——现在代码和文档都没提。

---

#### M14 · 模型不确定性　[RISK / minor]

**现象**：`temperature=0.0` 是默认值（`openai_lite.py:86`），但模型名、温度、种子从未被记录到事件或快照里。

**证据**：`grep -rn "temperature\|model\b" src/kernel/` — `src/kernel/` 里零命中。`LlmReply`（`src/kernel/ports.py:18-24`）只有 `text` / `tool_calls` / `tokens` 三个字段，没有模型标识。`openai_lite.py:117` 把 `self.model` 放进请求 payload，但响应侧的 `model` 字段被丢弃（`_reply_from` 不读它）。

**后果**：条件性风险。默认 `temperature=0.0` + 教学示例全用 `ScriptedLlm`（完全确定性），所以默认路径没问题。但只要有人传 `temperature=0.7`（构造参数支持），或者 `OPENAI_MODEL` 环境变量指向一个非确定性模型，就**无法区分"loop 逻辑有 bug"和"模型抽风"**——事件日志里没有模型名和温度，出了问题没法归因。

**建议**：在 `LlmReply` 加两个可选字段（`model: str = ""`, `temperature: float | None = None`），在 `_reply_from` 里从 `raw.get("model")` 取出来，在 `NODE_COMPLETED` 事件带上。成本是两个字段，收益是"这次输出能不能复现"变成可回答的问题。

---

### L 层（宏观 Loop Engineering）

#### L2 · 预算下传　[GAP / major]

**现象**：`max_depth` 是全局共享的（`scheduler.py:96` 递减到 0 为止），但**每个子 Run 都拿一份全新的 `max_waves=64`**。没有任何跨 Run 树的聚合预算。

**证据**：
- `src/kernel/scheduler.py:96` — 深度递减与守卫（这是对的）。
- `src/kernel/scheduler.py:282-286` — 子 Run 的 scheduler 复用同一个 scheduler 对象：`child_scheduler = self`，所以 `max_depth` 共享、`max_waves` 也共享**同一个数字**。
- `src/kernel/run.py:104` — 每个子 Run 的 `metrics` 从 `{"waves": 0, ...}` 重新开始。
- `src/runtime/agent.py:164-171` — `Agent._scheduler()` 构造 Scheduler 时**根本没传 `max_waves`**，用默认 64。`teammates` 委派（`agent.py:121-130`）走 `_run_standalone` → `_execute` → 一个新 Scheduler → 又一份 64。
- `Workflow.__init__`（`workflow.py:114-131`）允许用户设 `max_waves`，但只作用于这个 Workflow 自己。

**后果**：父 Run 3 波内嵌一个子 Run 跑满 64 波，父计数只走 1。深度守卫拦住的是"嵌套层数"，拦不住"每一层的总工作量"。8 层深度 × 每层 64 波 × 每波 N 次 LLM 调用，理论上可以指数放大。`max_depth=8` 看着是个上限，实际上它保证的是**每层都有完整的 64 波预算**。

**建议**：引入一个跨 Run 树的**共享计数器**，随 `activate()` 下传：

```python
# 挂在 Scheduler 上，子 Run 复用同一个
self._budget = {"waves": 0, "llm_calls": 0, "tokens": 0}
```

`SubagentActivator.activate()`（`scheduler.py:274-286`）里把 `_budget` 传给子 Run，让子 Run 的 `drive()` 检查的是同一个字典。这样 M1 和 L2 是同一个改动。

---

#### L3 · 委派广度　[PENDING / major]

**现象**：递归**深度**有守卫（`max_depth`，`scheduler.py:83-91`，全库最漂亮的一段防御），递归**广度**没有上限。

**证据**：
- `src/kernel/scheduler.py:83-91` — 深度守卫，注释写得好：*「Blocking it here is far more reliable than trusting the model to 'remember not to call each other'」*。
- `src/kernel/run.py:207-217` — `add_instance()` **没有实例数上限**，每次 `Send` 都新增一个节点。
- `src/kernel/graph.py:204-219` — `ready()` 把全部动态实例都当就绪。
- `src/kernel/scheduler.py:265` — 信号量限并发度，但不限本波实例**总数**。

**后果**：模型在一次输出里发 1000 个 `Send`（或返回 1000 个 tool_calls），图里就有 1000 个节点，下一波全部就绪，8 个并发地跑 1000 次 LLM/工具调用。`max_waves` 完全拦不住这个——它只走了 1 波。这不是假想：`plan_first.py:55` 的 `sends = [Send("worker", step, key=step["id"]) for step in steps]` 里，`steps` 的数量由模型输出的编号列表长度决定，没有任何裁剪。

**为什么标 PENDING 而不是 GAP**：`plan_first.py:43-45` 的 `parse_numbered_list` 是教学解析器，确实不限制步数。但**模型自己生成的 steps 数量**可能由外层 prompt 约束（示例代码里通常 prompt 写"分成 3-5 步"）。代码层面无上限是确定的，但"实际会不会出问题"取决于调用方 prompt，这属于需要确认的项。

**建议**：两处各一行：
1. `run.add_instance()`（`run.py:207`）加 `if len(self.instances.get(template, ())) >= self.max_instances: raise`，让超限是**构建期错误**而不是运行期爆炸。
2. `plan_first.py:55` 加 `steps = steps[:MAX_STEPS]`，超出的写进 `state_delta` 让 synth 知道被裁了。

---

#### L4 · 外层决策可判定性　[RISK / minor]

**现象**：绝大部分分支是机器可判定的，只有一处例外。

**证据**：
- `src/runtime/react.py:62` — `if reply.tool_calls:` 决定走 tools 还是 final，纯机器判定。
- `src/runtime/react.py:101` — `when=lambda s: bool(s.get("pending"))`，可判定。
- `src/runtime/react.py:103` — `when=lambda s: bool(s.get("answer"))`，可判定。
- `src/kernel/graph.py:244-245` — join 策略可机器判定（`any` / `all` / callable）。
- `src/kernel/graph.py:167-168` — `_edge_live()` 走 `bool(e.when(shared))`，可判定。
- **例外**：`src/runtime/multiagent.py:143-145` 的 blackboard moderator 判定"共识是否达成"靠模型判断，不是可机器判定的条件。

**后果**：blackboard 的多轮收敛完全依赖模型自己判断"我们达成共识了"。三个专家互相矛盾时，moderator 可能判定"达成"，也可能判定"未达成"再跑一轮——两次的差异无法从事件日志复现。这是收敛性风险，不是崩溃风险。

**建议**：把 moderator 的判定改成结构化输出（`{"consensus": true, "verdict": ...}`），让"是否收敛"变成机器可读的字段；`consensus=false` 时再跑一轮，上限 3 轮强制收敛到"记录分歧"而不是继续消耗。

---

#### L6 · 副作用幂等与去重　[RISK / major]

**现象**：`call_id` 作为幂等键的设计意图明确、文档写得清楚，但**同一个节点内多个 tool_calls 会拿到同一个 call_id**，而且**框架不强制去重**——它只是一个约定。

**证据**：
- `src/kernel/types.py:60-68` — `ToolCall.call_id` 的文档明确写了"at-least-once delivery + idempotency = exactly-once effect"。
- `src/kernel/body.py:177-180` — 幂等键的构造：
  ```python
  attempt = self.run.state_of(self.node_id).attempts
  call = ToolCall(name, arguments or {}, call_id=f"{self.run_id}:{self.node_id}:{attempt}")
  ```
- **碰撞点**：`src/runtime/react.py:81` — `for call in ctx.shared["pending"]:` 在一个节点 attempt 内循环调用多个工具，**每次 `ctx.call_tool()` 都算出同一个 `attempt`**，所以同一个节点的 5 个 tool_calls 有 5 个不同的 name/arguments 但共享同一个 `call_id`。
- **约定而非强制**：`src/runtime/tools.py:117-139` 的 `dispatch()` 完全没有读 `call.call_id` 做去重。幂等性完全依赖工具实现者自己记着检查 `call_id`。

**后果**：一个遵守约定的去重器看到 5 个 `call_id` 相同的调用，会把后 4 个当成重复调用跳过——但它们是 5 个不同的工具、不同的参数。`read_weather(city="北京")` 和 `read_weather(city="上海")` 会被当成同一次调用。这比不去重更糟：不去重是重复花钱，去重错是**静默丢操作**。

另一个断裂点：`Goto` re-arm 走 `reset_pending()` → `mark_running()`，会递增 `attempts`，于是 resume 之后 `call_id` 变了，同一操作的幂等键跨恢复不成立。检查点恢复（`scheduler.py:240-244`，suspend 总是落盘）之后重放已完成的副作用。

**建议**：幂等键必须包含调用本身的内容：

```python
digest = hashlib.sha1(f"{name}|{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()[:12]
call = ToolCall(name, arguments or {}, call_id=f"{self.run_id}:{self.node_id}:{attempt}:{digest}")
```

这样同一个 attempt 内的多个调用有各自的键，而同一节点重试同一调用（attempt 相同 + 参数相同）仍然能命中去重。另外建议在 `dispatch()` 里加一个**可选的**去重钩子（`registry.dedup_store`），让框架能强制而不是靠约定。

---

#### L7 · 失败收敛策略　[RISK / major]

**现象**：fail-fast 路径定义得非常清楚，但**没有升级人工的路径**。重试耗尽 = 整个 Run 失败，没有"停下来等一个人"的中间态。

**证据**：
- `src/kernel/scheduler.py:207-212` — 节点抛异常 → fail-fast，Run 失败。
- `src/kernel/scheduler.py:94-98` — 子 Run 失败向上传播，父 Run 一起失败。
- `src/kernel/scheduler.py:364-376` — `_settle()` 区分 "done" 和 "graph stalled: no ready node but unfinished nodes remain {pending}"，**响亮地失败**而不是给个半成品。这是 L7 里最该学的一条。
- `src/kernel/graph.py:37-63` — `RetryPolicy` 有 `max_attempts` / `timeout`（超时算一次失败），重试耗尽后向上抛。
- 但：`grep -rn "park\|Interrupt\|human" src/kernel/scheduler.py` — `park()` 只在 `run.py:52-59`（`run.park()`）和 `body.py:64-65`（`Interrupt`）暴露，**调度器从不自动调用它**。没有任何"重试 N 次后自动转入等待人工"的逻辑。

**后果**：一个 write 工具连续 3 次审批被拒（`bus.check()` 返回 `allowed=False`，`tools.py:130-131` 转成 `ToolResult.failure`），ReAct 的 `think` 收到 `[tool error] operation not approved`，可能换个参数再试，也可能再调同一个工具——3 次重试耗尽后整个 Run 失败，用户拿到的是失败，而不是"这个操作需要你批准"。而框架其实**已经有**人机交接的原语（`wait_human()`，`workflow.py:64-66`），只是调度器从不自动走到那里。

**建议**：在重试耗尽的分支（`scheduler.py:306-309`）加一条可选路径：`RetryPolicy` 增加 `on_exhausted: "fail" | "park"`，选 `park` 时走 `run.park(kind="approval_needed", question=f"{node_id} failed {max_attempts} times: {last_error}")` 而不是抛异常。这样现有的人机原语就能用上。

---

#### L8 · 重试策略与退避　[RISK / major]

**现象**：有指数退避，但**没有 jitter，没有最大延迟上限**；默认重试条件过宽。

**证据**：`src/kernel/graph.py:55-58`：

```python
def delay_for(self, failed_attempt: int) -> float:
    return self.base_delay * (self.factor ** failed_attempt)
```

- `src/kernel/graph.py:53` — `retry_on: tuple[type[Exception], ...] | Callable[[Exception], bool] = (Exception,)`，**默认重试所有异常**。
- `src/kernel/graph.py:60-63` — `matches()` 支持 tuple 或 predicate，这是好的。
- `src/kernel/scheduler.py:294-295` — `except asyncio.CancelledError: raise`，取消不被重试，这是好的。
- 无 jitter：`delay_for` 是纯函数，无随机性。
- 无上限：`factor ** failed_attempt` 无上界。

**后果**：`base_delay=1.0, factor=2.0, max_attempts=5` 时，延迟序列是 1/2/4/8，无上限意味着 `max_attempts=12` 就是 2048 秒。更实际的问题是 `retry_on=(Exception,)` 默认重试**包括程序 bug**：一个 `KeyError`（结构性错误，重试一万次也一样）会被退避重试 `max_attempts` 次，每次都是同样的崩溃，纯烧时间。而 `tools.py:138-139` 已经把工具异常转成 `ToolResult` 了，所以 `Node` 上真正会被 retry 的异常是模型调用失败和节点自身的 bug。

**建议**：两个小改动：

```python
def delay_for(self, failed_attempt: int) -> float:
    """Exponential backoff with full jitter and a cap."""
    import random
    raw = min(self.base_delay * (self.factor ** failed_attempt), self.max_delay)
    return random.uniform(0, raw) if self.jitter == "full" else raw
```

以及在 `Node.retry` 的默认值上给一个更窄的建议：文档明确写"默认 `retry_on=(Exception,)` 会重试 bug，生产场景建议收窄到 `(TimeoutError, ConnectionError)`"。

---

#### L9 · 跨 Agent 结果一致性　[GAP / major]

**现象**：supervisor 直接把 worker 输出当真相用，没有冲突检测，没有单一事实来源。

**证据**：
- `src/runtime/multiagent.py:51-62` — `build_pipeline()` 只是静态串联，上游输出直接喂下游。
- `src/runtime/plan_first.py:67-69` — `default_synth` 直接 `return Outcome.ok(inputs)`，把 N 个 worker 的输出列表原样返回，**不做冲突检测、不做汇总判断**。
- `src/runtime/multiagent.py:124-176` — blackboard 有 `join="all"` 的 moderator，但"共识"是模型判断（见 L4），没有结构化的冲突标记。
- `src/runtime/agent.py:137-141` — 委派返回 `run.final_output`，父 agent 拿到的是一个字符串，无法知道它和别的路径的结果是否矛盾。

**后果**：两个 worker 分别返回 "该 issue 已修复" 和 "该 issue 仍打开"，`default_synth` 把它们并列放进一个 list 返回。supervisor 的模型会自己决定相信谁——这个决定不可复现、不可审计、事件日志里没有记录。在 plan-first 场景里这很常见：两个 worker 查同一个事实但用了不同参数，结果不一致。

**建议**：这是需要新增机制的一项，不是补丁。最小可行做法：给 synth 节点一个**结构化输出**要求（`{"summary": ..., "conflicts": [{"fact": ..., "sources": [...]}]}`），并在事件日志里记录 `conflicts` 非空的情况。让"存在矛盾"成为一条**可观测事件**，而不是模型内部的一次判断。

---

#### L10 · 验收与 Goodhart 防护　[GAP / major]

**现象**：没有验收器，没有 done 判据（除了"所有节点到终态"），没有"不许做什么"的边界。

**证据**：
- `src/kernel/graph.py:281-286` — `is_done()` 只检查"所有节点到终态"，这是结构完成，不是任务完成。
- `src/kernel/scheduler.py:378-385` — `_final_output()` 无验收步骤。
- `grep -rn "verifier\|acceptance\|done_criteria" src/` — 零命中。

**后果**：一个 code agent 写完代码、跑了测试、测试全绿，就算 done。它如果顺手删了一个失败的测试用例，`is_done()` 仍然返回 True，Run 正常结束，输出看起来完全正常。这在教学示例里无害，但在任何生产用法里是 Goodhart 的经典入口。

**建议**：这个和 L9 一样属于需要新增机制。最小做法是在 `Plan` 上加可选的 `verifier: NodeBody`，在 `_settle()` 判 done 之前跑一次，verifier 的 Outcome 决定"通过"还是"打回"（`Goto` 回到某个节点）。注意 verifier 本身不能修改被验收的对象——这是验收器的基本约束。

---

### 跨层（X）

#### X1 · 可观测性　[GAP / major]

**现象**：有 9 种事件类型 + `NODE_RETRY`（带 attempt/error/backoff），但缺成本、缺 token、缺工具名/参数、缺决策理由。

**证据**：`src/kernel/eventlog.py:25-33` 的事件类型清单：`RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` / `RUN_SUSPENDED` / `NODE_STARTED` / `NODE_COMPLETED` / `NODE_FAILED` / `NODE_RETRY` / `STATE_DELTA`，共 9 种。

- `src/kernel/scheduler.py:302-305` — `NODE_RETRY` 带 `{"attempt": n, "error": str(exc), "backoff": delay}`，这条很好。
- **缺**：没有 COST 事件、没有 TOKENS 事件、没有 LLM_CALL 事件。模型调用的次数和 token 数只存在于 `run.metrics`（`body.py:167-168`），只在 Run 结束时随 `RUN_COMPLETED` 出现，**中间过程不可见**。
- **缺**：工具调用的 name 和 arguments 不进事件。`dispatch()`（`tools.py:117`）不 emit。想知道"这个 Run 调了哪些工具"只能读 `state["messages"]` 里的 assistant/tool 消息。
- **缺**：决策理由不进事件。`react.py:62` 的 `if reply.tool_calls` 是机器判定所以不需要记录理由，但 `multiagent.py:143-145` 的 moderator 判断"共识达成"是模型判断，没有理由记录。

**后果**：一次 Run 失败后，事件日志能告诉你"哪个节点失败了、重试了几次、退避了多少"，但**回答不了"这次花了多少钱"**（要手动从 metrics 反推）、**回答不了"哪个工具调用导致了问题"**（要读 messages 数组）。对一个有 64 波 × 8 并发的 Run，这个缺口会让人无法定位问题。

**建议**：最小补齐——在 `body.py:167-168`（`llm_chat` 返回前）和 `tools.py:133-137`（`dispatch` 成功前）各 emit 一个事件：

```python
await ctx.emit("llm_call", tokens=reply.tokens, tools=len(tools or []), call_id=f"{run_id}:{node_id}:{attempt}")
await ctx.emit("tool_call", name=name, args=arguments, call_id=call.call_id, ok=result.ok)
```

用已有的 `ctx.emit()`（`body.py:141-143`）就够了，不需要新增事件类型。

---

#### X2 · 可重放　[PENDING / major]

**现象**：事件日志能重建**状态**，但**重建不了这次运行**。

**证据**：
- `src/kernel/eventlog.py:45-62` — `apply_event()` / `fold_events()` 是纯函数，从 `STATE_DELTA` 事件重建 shared 状态。`STATE_DELTA` 存的是**波次增量**而不是全量，所以按波重放不会重复计数——这个设计很干净。
- **缺**：LLM 的回复文本不进事件。`llm_chat`（`body.py:166-169`）返回 `LlmReply`，只在节点里被消费，不 emit。
- **缺**：模型名、温度、种子不进事件（见 M14）。
- **缺**：`temperature=0.0` 默认（`openai_lite.py:86`）意味着默认路径**理论**上可复现，但事件里没有记录"这次用的是 0.0"，所以不可验证。

**后果**：一个 Bug 现场，你可以重放状态到出问题的波，看到当时的 shared 状态，但**看不到当时的模型说了什么**——而 agent 的所有行为都源自模型输出。没有模型输出，你就无法判断"是状态不对导致模型说错了"还是"状态对了但模型抽风"。

**为什么标 PENDING**：`ScriptedLlm` 是确定的，所以**测试场景**下事件日志 + 脚本就足以复现。这是教学库的定位决定的。但**生产场景**（`OpenAICompatibleLlm`）下缺口是真实的。所以这项取决于项目实际用途，标 PENDING。

**建议**：在 `llm_chat`（`body.py:166`）emit 一个带 `reply.text` 和 `reply.tool_calls` 的事件。成本是日志体积，收益是完整的因果链。可以考虑只对**决策性**的调用（带 tool_calls 的）记录完整文本，纯文本调用只记 hash，控制体积。

---

#### X3 · 人机交接　[PENDING / major]

**现象**：有三条真实的人机缝——审批门（`write_needs_approval`）、暂停-恢复（`Interrupt` + `park()` + `resume()`）、以及恢复时的严格 key 匹配。但"什么时候该问人"是模型判断的。

**证据**：
- `src/kernel/run.py:52-59` — `Run.park()` / `resume()`，严格匹配 pending interrupt 的 key。
- `src/kernel/body.py:64-65` — `Outcome.park()`。
- `src/kernel/scheduler.py:141-170` — `resume()` 实现，带 key 匹配。
- `src/runtime/workflow.py:64-66` — `wait_human(question, payload, kind)` 的公共 API。
- `src/kernel/scheduler.py:240-244` — suspend **总是**落盘，跨进程可恢复（配合 `file_store.py`）。
- **但**：恢复时 dict-vs-裸值的消歧是启发式的（`scheduler.py:159-164`）——如果 resume 值是一个 dict，它被当状态更新；如果是裸值，被当回答。这个约定没有类型约束，用户传错会静默走错分支。
- **但**：`Interrupt.kind`（`run.py:52`）只是字符串，没有枚举校验。`"approval"` / `"more_info"` / 任何拼错的字符串都合法。
- **但**：谁决定"这里需要问人"？`workflow.py:64` 的 `wait_human()` 是**显式调用**的，只有 prompt 里写"遇到 X 就调用 wait_human"才生效。框架不自动升级（见 L7）。

**后果**：人机缝的**基础设施**是完整的（暂停、恢复、跨进程、乐观并发），但**触发条件**全靠 prompt 自律。一个没写"调用 wait_human"的 prompt，遇到需要人工的决策点会走 fail-fast（L7）。恢复时的 dict 歧义也会咬人：如果外部系统恢复时传了一个 `{"decision": "approve"}` 而不是 `"approve"`，它被当成状态更新，回答丢失，节点拿不到 approval 就再次 park。

**建议**：
1. `Interrupt.kind` 做成枚举校验（对齐 X5 的白名单模式）。
2. `resume()` 的 dict-vs-裸值歧义改成显式：`resume(run_id, value=None, *, as_value: bool = True)`。
3. 把"自动升级到人工"接上 L7 的 `on_exhausted="park"`。

---

#### X4 · 环境一致性　[GAP / major]

**现象**：整个模型选择在运行期静默切换，无配置文件、无显式声明。

**证据**：`src/runtime/llm.py:50-58`：

```python
def env_llm(fallback: Any):
    """Use a real OpenAI-compatible model if OPENAI_API_KEY is set, else the fallback."""
    if os.getenv("OPENAI_API_KEY"):
        return OpenAICompatibleLlm()
    return fallback
```

- `src/runtime/openai_lite.py:90-100` — 4 个环境变量静默影响行为：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_MAX_TOKENS`。
- `src/kernel/scheduler.py:116-119` — 默认值硬编码在构造参数里（`max_waves=64, concurrency=8, max_depth=8, durability="sync"`）。
- 无配置文件、无环境标识、无"当前配置"输出。

**后果**：同一份代码，开发机上有 `OPENAI_API_KEY` 就用真模型，没有就用 `ScriptedLlm`。**行为差异是巨大的**：`ScriptedLlm` 完全确定，`OpenAICompatibleLlm` 不确定且会截断（M10）、会丢 JSON（M7）。一个测试在开发机上过了，到另一台机器上 `export OPENAI_API_KEY=sk-...` 之后行为完全不同，而且**没有任何日志告诉你切换了**。`OPENAI_MODEL` 更隐蔽——改一个环境变量就把模型换成完全不同的一个，`max_tokens` 预算也失效（`openai_lite.py:97-99` 的注释自己承认不同模型预算消耗差异巨大）。

**建议**：两件事。第一，`env_llm()` 在切换时**显式记录**——至少 emit 一个 `RUN_STARTED` 里带 `model_kind="scripted"|"openai"` 字段。第二，把 4 个环境变量收进一个 `ModelConfig` dataclass，让"这次用了什么配置"变成可查询的对象而不是散落在 env 里的隐式状态。

---

#### X5 · 配置漂移　[RISK / major]

**现象**：部分配置项有白名单校验（做得很好），但数字类配置项完全无校验，`concurrency=0` 会死锁。

**证据**：
- **好的一面**：`src/kernel/scheduler.py:121-122` — `durability` 白名单校验，拼错直接 `ValueError`。
- **好的一面**：`src/kernel/bus.py:103-104` — `on_full` 白名单校验（`"block"` / `"drop"`），拼错直接 `ValueError`。
- **好的一面**：`src/kernel/graph.py:119-141` — `Plan.validate()` 在**构建期**失败：悬挂边、未知 entry、`join` 策略拼错（`"alls"`）都报。
- **坏的一面**：`src/kernel/scheduler.py:116-119` — `max_waves` / `concurrency` / `max_depth` 接受任意 int，**零校验**。
- `src/kernel/scheduler.py:265` — `asyncio.Semaphore(concurrency)`，传 0 时信号量永远不可用，所有节点永远等，**死锁**而不是报错。

**后果**：`Scheduler(concurrency=0)` 或 `Scheduler(max_waves=-1)` 都会静默接受。`concurrency=0` 是硬死锁——一个波都没法启动，进程卡住，没有异常、没有日志。`max_waves=-1` 永远不会触发上限检查（`metrics["waves"]` 从 1 开始递增，永远不大于 -1），等于**无上限**。这两种都符合 X5 的定义：配置值错误 → 静默失效。

**建议**：在 `Scheduler.__init__` 里对齐已有的白名单校验风格，一行一个：

```python
if self.concurrency < 1:
    raise ValueError(f"concurrency must be >= 1, got {self.concurrency}")
if self.max_waves < 1:
    raise ValueError(f"max_waves must be >= 1, got {self.max_waves}")
if self.max_depth < 1:
    raise ValueError(f"max_depth must be >= 1, got {self.max_depth}")
```

本库已经在 `durability` 和 `on_full` 上用了这个模式（fail-closed on typo），把数字类配置补上就是完整的一致策略。

---

## 3. 框架差异结论

**整体跳过。** 本库自研，无任何框架可映射——按 `references/framework-map.md` 的处理规则，"无框架（自研 loop）"跑不变式层全量检查，不做框架映射。

这一节本来要写的框架特有坑（LangGraph 的 M3 换算比、M9 并发写抛异常等）在这里没有对应物。但**本库自己的引擎制造了一个框架式的坑，值得记录**：

### 自研 BSP 波次引擎特有的坑

- **M3 换算比不固定**：`max_waves` 数的是波，一个波 = 本波就绪节点数，这个数由 `Send` 和模型 tool_calls 决定，无上界。见 M3。
- **SubPlanBody 是"波内的 Run"**：一个波里可以嵌一整个 64 波的子 Run，父计数只走 1。这是比任何框架的换算比都极端的形态。见 M3 / L2。
- **波次模型的代价**：本库自己在 `docs/zh/design/03-waves.md:37-45` 写清了——straggler 效应，同波一个慢节点让其余节点在屏障处空等；并给出"拆细慢节点或用子 Run 隔离，不要拆掉一致性边界"的应对。**作者知道代价，并且给了取舍理由**，这比大多数框架的默认行为都清醒。

---

## 4. 值得吸取的实践（本库做对了、本技能应纳入的部分）

分两组，因为 M 层和 L 层的实践成熟度差别很大：**L 层（宏观 loop engineering）是这个库真正的强项**，下面按"迁移成本 / 收益比"排序。

### 4A. L 层实践（宏观，本库的强项）

1. **BSP 波次屏障 + 声明式状态通道 + `allow_multi`，把并发写变成不可表达的状态。** `channels.py:49-95`（通道声明 `allow_multi` / `empty` / `dtype`）、`channels.py:135-147`（`check_ambiguous()` 在屏障处对 `last` 通道多写入者抛 `AmbiguousWrite`）、`run.py:224-243`（先在本波内从 `empty` 聚合，再折进历史——所以重放不重复计数）。节点**永远不写共享状态**，只返回 `Outcome(state_delta)`；屏障一次性折叠。这把 M9 从"检测问题"变成"不可表达问题"，是本次审计里最值得进技能的一条。

2. **未声明通道的写入 = 硬错误，并且指名是哪个节点写的。** `channels.py:122-127` 抛 `KeyError` 并把 writer 节点 ID 放进消息；`128-132` 在边界处校验 `dtype`。一个分支捕获一整类拼写错误，收益极高。

3. **`Goto.now` vs `Goto.rejoin` 显式区分"重激活但要等前驱"和"立刻就绪"。** `command.py:36-71`。这个区分解决的是多轮收敛点的竞态：ReAct 回边用 `now`（`react.py:70`），blackboard 的多轮 fanout 用 `rejoin`（`multiagent.py:156`），plan-first 重规划时 re-arm synth 也用 `rejoin`（`plan_first.py:64`）。**没有这个区分，`Goto` 回边会和前驱抢在同一波里读空结果**。这是循环正确性的关键细节，本技能 L1/L4 没覆盖。

4. **`_settle()` 区分"完成"和"卡住"。** `scheduler.py:364-376` — "graph stalled: no ready node but unfinished nodes remain {pending}"，把接线错误变成响亮的失败，而不是静默的部分答案。配套 `graph.py:257-279` 的 `sweep_skipped()` 只在"本波无就绪节点"时做死分支清理，**保证它不会杀死可能被回边激活的分支**。这条同时回答了 L1 和 L7 的"如何发现漏了边"。

5. **两级错误策略的命名。** 模型级失败 → 反馈给模型（`tools.py:122-126`、`138-139`；`react.py:83` 的 `"[tool error] {error}"`）；结构性失败 → fail-fast 整个 Run（`scheduler.py:207-212`）。这个"retry-loop vs fail-fast"的切分是本次审计里最可迁移的想法——本技能 M8/L7 只描述了现象，没有给命名。

6. **构建期 `Plan.validate()`。** `graph.py:119-141` — 悬挂边、未知 entry、`join` 策略拼错（`"alls"`）全部在构建期失败，而不是运行期。配合 `158-163` 的 `terminal_ids()` 兜底（没标 terminal 就用"没有出边的节点"）和 `167-168` 的 `_edge_live()` 把条件边求值隔离成纯函数。

7. **深度守卫作为结构性强制，而不是模型自律。** `scheduler.py:83-91` — "Blocking it here is far more reliable than trusting the model to 'remember not to call each other'"。注释本身就是 L3 的立场，可以直接抄。

8. **枚举白名单校验在构造期 fail-closed。** `scheduler.py:121-122`（`durability`）、`bus.py:103-104`（`on_full`）。这是 X5 的正确解法——**校验配置键，不要回落到默认值**。

9. **审批 fail-closed。** `bus.py:136-148` 的 `check()`：checker 抛异常时返回否决票（`BlockingResult(False, ...)`），**一个坏掉的审批器是否决，永远不会变成放行**。配套 `fire` / `check` / `collect` 三协议把"观察 / 裁决 / 收集"分离。这条应进 M13 / X3。

10. **背压的两个策略是具名的、可数的。** `bus.py:54-64` — `on_full="block"` 让上游背压，`"drop"` 时 `self.dropped` 计数，让丢弃变成可观测的。示例 `examples/11_backpressure.py` 展示了这个 tradeoff。

11. **wiring-vs-data 边界。** `body.py:103-110` — context 持有活对象（模型端口、工具端口），永不序列化；数据只走 `input` / `state_delta`。注释原文："that boundary keeps checkpoints clean"。这条让检查点自动干净，不需要额外机制。同时 `ctx.shared` 是只读视图（`body.py:131-135`），要改状态必须返回 `state_delta`——**把"节点直接改状态"这个错误模式变成不合法调用**。

12. **事件溯源的记录波次增量而不是全量。** `eventlog.py:45-62` 的 `apply_event()` / `fold_events()` 是纯函数，`STATE_DELTA` 存**增量**，配合 `run.py:224-243` 的两阶段折叠，按波重放不会重复计数。X2 的正确形态。

13. **原子分组裁剪上下文。** `context.py:99-123` 的 `_tool_groups()` 把"工具调用父消息 + 其结果"当成不可拆的原子组，`context.py:233-241` 的切分点回退到原子组边界——**永不留下孤儿 tool result**，孤儿会让模型直接报错。这是 M9 的上下文侧对应物，本技能没写。

14. **压缩分级：机械手段优先，只有摘要级才花模型的钱。** `context.py:79-96` 五级 + `172-179` 按填充率选级：L0 不动 → L1 只截短超长工具结果（不删消息、不调模型，`_shrink_tool_text` 保留头尾）→ L2/L3 摘要 → L4 紧急兜底（保留最近 2 条 + 最近一次摘要）。`last_level` 字段（`context.py:164-166`）让"选了哪一级"可观测。"先机械、再花钱"的顺序值得进 M9 的压缩建议。

15. **原子写检查点 + 乐观并发。** `backends/file_store.py:22-25` — 临时文件 + `os.replace`，任何时刻崩溃都不会留下半写检查点；`36-51` 的 `expected_version` 支持乐观并发，写冲突抛 `RuntimeError` 而不是覆盖。

16. **空模型流大声失败，并保留原始片段。** `openai_lite.py:200-209` — 保留 3 条原始 SSE 分片作诊断。**同一文件的 `except JSONDecodeError: args = {}` 就是它的反面教材**（M7），这个正/反对照本身就是最好的教学材料。

### 4B. M 层实践（微观，本库的合格线）

17. **`side_effect: read|write` 分类 + 执行前审批门。** `tools.py:58`、`tools.py:128-131`。真实的权限边界——但注意本库自己给出了三个反例（`multiagent.py:89` 委派标 read、`mcp.py:78` MCP 全标 read、无 prompt injection 处理），说明"分类"本身不够，**分类的覆盖完整性才是问题**。这条进 M13 时要连反例一起。

18. **两层调用约定兼容。** `tools.py:141-167` 的 `_invoke()` 同时支持"业务函数按名注入参数"和"适配器函数接收整个 arguments dict"。这让 MCP 工具、本地函数、子 agent 委派走同一条 pipeline。

19. **零依赖的模型适配层 + 流式与非流式同归一。** `openai_lite.py:77-145` — stdlib urllib 手写 POST，流式与非流式最终都走 `_reply_from()` 归一成 `LlmReply`。`86-100` 的 `max_tokens` 默认 8192 并注释了"reasoning 模型会把思考算进输出预算"的原因，这是**有理由的默认值**。

20. **`coerce_outcome()` 的列表判定很克制。** `body.py:77-92` — 一个 list 只有当**所有元素**都是 Command/Outcome 时才被当控制组，否则当业务值。避免了"返回一个排序好的结果列表被误读成控制命令"这类误判。

21. **可替换策略而非硬编码。** context（`context.py:24-25` 的 `ContextManager` Protocol）、memory（`memory.py:32-36` 的 `Memory` Protocol）、skills 全部是可选注入的策略，`react.py:46-52` 只判断 `is not None`。kernel 完全不知道它们存在。这符合本技能 M11 的"机制固定、策略可替换"。

22. **测试锁住行为而不是实现。** 17 个测试文件、约 69 个测试函数，锁了 `AmbiguousWrite`（`test_channels.py:38`）、`max_waves` 防自旋（`test_graph_scheduler.py:124`）、backpressure（`test_backpressure.py`）、subrun 深度守卫（`test_subrun.py`）、纯函数性（`test_purity.py`）。注意：**没有预算/成本相关测试**，与 M1 的判定一致。

---

## 5. 待确认清单

| 项 | 要确认什么 | 怎么确认 |
|---|---|---|
| L3 | 模型生成的 `Send` 数量在实际使用中是否有外层约束 | 读调用方 prompt；或给 `parse_numbered_list`（`plan_first.py:35-45`）加断言后跑全套示例 |
| X2 | 生产场景下是否需要重建 LLM 输出 | 取决于项目实际用途：教学（`ScriptedLlm`）已可复现；生产（`OpenAICompatibleLlm`）缺口真实 |
| X3 | resume 的 dict-vs-裸值歧义是否咬到实际调用方 | 读 `workflow.py:232-236` / `agent.py:190-194` 的调用方，看传的是 dict 还是字符串 |
| M14 | `temperature>0` 的实际使用场景 | `grep -rn "temperature=" examples/ tests/`，确认是否有人覆盖默认 0.0 |
| X5 | `concurrency=0` 死锁是否真的可达 | 写一个 `Scheduler(concurrency=0)` 的最小复现（预期：永久挂起，无异常） |

---

## 6. 建议修复顺序

按「阻断级 → 一行能改的 → 需要重构的」排：

**1. M1（阻断）+ L2 + M3 —— 同一个改动。** 在 `scheduler.py:190` 的既有检查旁边加 `max_llm_calls` / `max_tool_calls` / `max_tokens`，并用一个跨 Run 树的共享字典下传。这一改同时消掉三个 finding，而且**把 M3 的换算比问题变成非问题**（不再需要用波数估算成本）。

**2. M7（几行）。** `openai_lite.py:222-223` 和 `229-230` 的 `except: args = {}` 改成带诊断的显式失败。与本文件 `200-209` 的空流处理对齐。

**3. X5（三行）。** `Scheduler.__init__` 加 `concurrency >= 1` / `max_waves >= 1` / `max_depth >= 1` 校验。消除 `concurrency=0` 死锁。

**4. M6（十行）。** `tools.py:126` 之后加最小类型校验，走 `ToolResult.failure` 反馈通道，不炸图。

**5. M13 绕过点 2（一行）。** `mcp.py:78` 的 `side_effect="read"` 改成默认 `"write"` 或加显式声明参数。这一行堵住的是"用户以为有审批但实际没有"——比 M13 的其他问题都更危险，因为它破坏了信任。

**6. L6（几行）。** `body.py:180` 的幂等键加入参数摘要，消除同 attempt 内多调用的键碰撞。

**7. M10（几行）。** `LlmReply` 加 `truncated` 字段，`_reply_from` 读 `finish_reason`。

**8. L8（几行）。** `graph.py:55-58` 的 `delay_for` 加 jitter 和 `max_delay` 上限；文档标注 `retry_on=(Exception,)` 会重试 bug。

**9. L7 + X3（需小改机制）。** `RetryPolicy.on_exhausted="park"`，把已有的 `wait_human()` 原语接到重试耗尽路径。

**10. X1（几行 emit）。** 在 `body.py:166` 和 `tools.py:133` 各加一个 `ctx.emit()`，补齐成本/工具可见性。

**11. L4 / L9 / L10 / X2（需要新增机制）。** 结构化输出契约、冲突检测、验收器。这些是本库真正缺的能力，但都在教学定位的边界外——建议先在文档里标注为"已知缺口"，而不是一下子补全。

---

## 7. 关于本审计自身的注记

- **只读**：未修改被审计项目的任何文件。源码通过 `gh api` 拉到本地 `/tmp/paf/`（GitHub 443 端口 `git clone` 连续 6 次失败，改用 API 逐文件抓取，100/100 文件完整）。
- **证据等级**：全篇 `[源码确认]`（本地 file:line）。无 `[自省实测]`（本机未安装该库，也无外部框架可自省）；无 `[版本知识]` 升级的结论。
- **未读的文件**：`src/playground/server.py`、`src/kernel/__init__.py`、`examples/`、`docs/` 全文。这些不影响 M1–M14 / L1–L10 / X1–X5 的判定，但如果要精确计算"实际部署配置的漂移面"，`playground/server.py` 值得补读（它暴露了交互式入口，可能有未审计的配置路径）。
- **测试覆盖**：17 个测试文件、1233 行、约 69 个测试函数，锁住了大部分行为。`tests/` 里**没有**预算/成本相关的测试——这与 M1 的判定一致。

---

## 8. 追加发现：用升级后的技能（v1.3.0 正向判据）重查

第 1–7 节用的是 v1.2.0 的不变式表。v1.3.0 给表格加了「正向判据」——不再只问「漏点犯没犯」，而是问「做得好的那条线达到了没有」。用新判据重跑了一遍，**多出 2 项发现**，且第 2 项是原判 M2 没说清的关键区别。

### 8.1 M2 · 检查点位置　[GAP / major]（原判 RISK / minor，升级）

**原判只说**：唯一的上限检查在循环内、每波执行一次，位置是对的；风险是「以后有人在节点内部加检查会多发一轮」。

**新判据问的是**：每一条进入引擎的构造路径，都接到配置了吗？——答案是**否，而且两条路径行为不同**。

| 路径 | 位置 | 上限是否继承 |
|---|---|---|
| `SubPlanBody` → `ctx.spawn` → `SubagentPort.activate` | `body.py:183-189`、`scheduler.py:82-105` | **继承**。`activate` 调的是 `self.scheduler.drive(spec, child)`——复用父 Scheduler，父设 `max_waves=1`，子 Run 也拿 1 |
| `Agent` → `Agent._scheduler()` | `agent.py:164-171` | **不继承**。这里 `new` 出一个全新 Scheduler，**既没传 `max_waves` 也没传 `max_depth`**，全部落回默认 64 / 8（`scheduler.py:116-118`） |

**后果**：一个 Workflow 设了 `max_waves=1` 想让每次 run 只跑一波，`SubPlanBody` 节点里的子 Run 也守这个限制，但**只要同一个 Workflow 里有一个节点是 `Agent`，那个 Agent 就能跑满 64 波**。同一个门面里两种上限语义并存，而且没有任何日志说明走了哪条。这正是新判据指向的形态：不是「配置错了」，是**这条路径根本没接到配置**，所以常规配置审计看不出来。

**修法**：`Agent._scheduler()` 补传 `max_waves` / `max_depth`，并在 `Agent.__init__` 加这两个参数（默认仍 64/8，但显式化）。三行改动，同时消掉 L2 的一部分。

**为什么原判漏了**：原判按「检查点位置对不对」判，位置是对的，所以只给了 minor。新判据按「所有路径是否一致」判，答案是否，所以是 major。**同一段代码，两个判据给出不同严重度**——这是「正向判据不是加分项、是准入条件」的直接体现。

### 8.2 M9 · 状态通道声明　[RISK / minor]（新增项，原判 OK 的正面被收窄）

原判 M9 判 `OK`，理由是本库的声明式通道 + reducer + `allow_multi` 把并发歧义变成不可表达状态（`channels.py:49-95`、`run.py:224-243`）。这个判断在**内核层**依然成立，但**门面层放宽了一部分**，原判没查。

**现象**：`_FacadeBody.run()`（`workflow.py:83-85`）会给任何未声明的 state key **自动补一个 `last` 通道**：

```python
for k in outcome.state_delta:
    if k not in plan.channels:
        plan.channels[k] = last(None)
```

而内核层对同一行为是硬错误（`channels.py:122-127`，抛 `KeyError` 并指名哪个节点写的）。

**为什么不是严重缺陷**：`last()` 工厂把 `allow_multi` 置为 `False`（`channels.py:80`），所以**并发歧义仍然被 `check_ambiguous()` 挡住**（`channels.py:135-147`）。最要紧的那道防线在门面层依然有效。

**真正丢掉的三样**：

1. **拼写错误的拦截点从构造期后移到运行期**——`"verdit"` 而不是 `"verdict"` 在内核层一调用就报错，在门面层静默变成新通道，跑完全程无人知晓。
2. **`dtype` 守卫无法通过门面层的自动补通道生效**（`channels.py:128-133` 的类型校验依赖声明时的 `dtype`，自动补的通道没有）。
3. **用户从门面层学到「写什么 key 都行」的习惯会带进内核层**——同一个库，两层策略不一致。

**修法**：给 `Workflow` 加一个显式开关，默认严格：

```python
def __init__(..., *, allow_undeclared_state: bool = False):
```

严格模式下 `_FacadeBody` 不自动补通道，落到内核层报错；文档说明「要宽容就显式打开」。这样门面层的宽容度从「默认静默」变成「显式选择」，与内核层策略对齐。

**附带的正面结论**：黑板模式（`multiagent.py:124-176`）用的是内核层 `Plan` 而非门面层，`board` 显式声明为 `append()`（`:168`）——**多写点的键该声明成可多写的 reducer，而不是 `last`**。这条正确实践在门面层用户看不到，因为门面层永远只给 `last`。

### 8.3 重查后未变动的判定

以下项在新判据下**结论不变**，列出以免误读成遗漏：

- **M1（阻断）**：正向判据要求「计数被读作上限」，本库三个计数器都在计数但无一处被读作上限（`body.py:167-168,177` 写，`scheduler.py:191` 只读 `waves`）。原判 blocker 成立且更硬了。
- **L3（PENDING）**：新判据要求深度与广度分别判。深度有守卫（`scheduler.py:83-91`），广度仍无上限（`run.py:207-217`）——原判 PENDING 的理由不变。
- **M13（RISK）**：新判据的三步问（分类取值 / 谁走了默认值 / 默认值代表什么）正好锁定原判已列的三条旁路（`multiagent.py:89`、`mcp.py:78`、无注入处理）。结论一致。
- **L6（RISK）**：新判据三步问（有没有键 / 粒度 / 谁在读）——本库第 1 步答得上、第 2、3 步答不上，与原判定性完全吻合。
- **X5（RISK）**：新判据要求枚举与数字分开判。枚举五处全 fail-closed，数字零校验——原判已按这个区分写下，结论一致。
- **M1 / M6 / M12 / M14 / L2 / L9 / X4**：这七项在 v1.3.0 的实践库里**没有正向锚点**（挂载索引里的空行）。另有 **L10 只有半个锚点**——模式 3 的 fail-fast 命名只解决了「死得响不响」，没解决「挂起等人工」和「验收器」两半。这八项无法用「做到了没有」判，只能按原表判 GAP/RISK。**这是判据缺失，不是项目缺失**——报告里要写明，避免读者以为「技能说它该怎样但没写」。

### 8.4 重查的方法学注记

新判据让审计从「核对漏点清单」变成「问一个更高的问题」，代价是**同一份代码可能给出不同严重度**（见 8.1）。所以判据必须是**准入条件**而不是加分项：满足负向漏点未犯、但不满足正向判据的，一律判 `RISK` 而非 `OK`。本次重查里改判的只有两项——8.1 的 M2 升级、8.2 的 M9 降级，其余 27 项结论不变，说明判据是收敛的，不是把 `OK` 批量打成 `RISK`。

