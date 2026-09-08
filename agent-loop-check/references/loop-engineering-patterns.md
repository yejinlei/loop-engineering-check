# Loop Engineering 优秀实践库（正向锚点）

本文件是 `SKILL.md` 不变式表的**正向面对**：不变式表回答「缺什么会怎样」，本文件回答「做得好的长什么样、怎么搬过去」。

做法来自**六个真实案例**，全部锚点均为 `[源码确认]`，核验日期 **2026-09-07**（案例 1–2）与 **2026-09-08**（案例 3–6）。没有一条是设计意图推测。

| | 案例 1 · 自研 BSP 波次编排引擎 | 案例 2 · deepseek-harness | 案例 3 · pi | 案例 4 · OpenHarness | 案例 5 · harness/harness | 案例 6 · hermes-agent |
|---|---|---|---|---|---|---|
| 定位 | 匿名（按用户要求脱敏）的本地教学代码库 | [公开仓库](https://github.com/deepseek-ai/deepseek-harness)，TypeScript，Cordis 插件框架 | [公开仓库](https://github.com/earendil-works/pi)，TypeScript，通用 agent 运行时 | [公开仓库](https://github.com/HKUDS/OpenHarness)，Python，agent 运行时 | [公开仓库](https://github.com/harness/harness)，Go，**非 LLM** 的 CI/CD 平台 | [公开仓库](https://github.com/NousResearch/hermes-agent)，Python，面向终端用户的完整 agent 产品（CLI / TUI / desktop / API server） |
| 产出 | 模式 1–17：11 条做法 + 6 条缺陷反例 | 模式 18–23：6 条做法；模式 24：3 条反例合集 | 模式 25–31：7 条做法 | 模式 32–34：3 条做法；模式 35：3 条反例（含 1 条正例） | 模式 36–39：4 条做法；模式 40：1 条反例 | 模式 41–51：11 条做法；模式 52–54：3 条反例 |
| 强项 | 状态合并规则、循环终态区分、审批分层 | 重试退避、配置不变量、事件持久化 | 流式完整性、批终止语义、恢复与清理 | 终态可区分、预算记账、非对称钳制 | 领域泛化证据、唤醒兜底、终态编码 | 多运行模式下的护栏分层、成本×次数交叉、配置解析拒绝翻转值 |

六个案例互补而非互证：案例 1 在状态与循环控制上最扎实，但在预算与重试上全空；案例 2 在预算与重试上最扎实，却**自文档化**了「无内建轮次预算」；案例 3 在流式完整性与恢复语义上最扎实，是全库唯一把「为什么」写进注释的项目；案例 4 给出了 L1「三条终态」里缺失的那一条（无上限退出）；案例 5 **不是 LLM 项目**，它证明了这组不变式是调度类系统的通用属性，不是 LLM 特有；案例 6 是终端 agent 产品，多数锚点是「同一套不变式在不同运行模式下的分层」，而不是新做法。

**「没有内建预算」不是批评，是设计选择**——它写在 Known Limitations 里，所以是反例（可参照的边界），不是缺陷指控。

**引法有两条，要求不同：** 引「做法」可以直接迁移，不需要版本确认；引「行号」要先确认目标项目是否同一版本——案例 1 是小型教学代码库，案例 2–6 都是活跃迭代的上游主分支（`master` / `main`），行号都会在迭代中漂移。

## 0. 总纲：把错误变成不可表达，而不是写成检查

本文件里所有做法共享同一个设计立场——**优先让错误的状态无法被构造出来，而不是在运行时检查它**。

| | 做法 | 例子 |
|---|---|---|
| 结构性不可表达 | `ctx.shared` 是只读视图，节点想改状态必须返回 `state_delta`，由引擎在屏障处统一折叠 | `src/kernel/body.py:131-135` |
| 运行时检查 | `dispatch` 只检查 required 缺没缺，类型不对照样执行 | `src/runtime/tools.py:122-126` |

同一个库同时有这两种写法，正好示范了差距：**检查型的防线永远要跟「有多少条分支」赛跑**。

审计时判 `OK` 要分清是哪一种：

- 结构性防线 → 可以判 `OK`。
- 检查型防线 → 判 `OK` 但要追加一个问题：**这个检查覆盖了几条分支？** 答案不清楚时降级为 `RISK`，不要判 `OK`。

---

## 模式 1 · 声明式状态通道：状态合并规则在写入前就被定死

**服务的不变式：** M9、X2

**做法（四条硬规则）：**

1. 每个状态键在 `Plan` 构建时声明一个 reducer：`last` / `append` / `add` / `merge`。
2. `Channel` 声明时同时给 `empty`（恒等元素，用于无写入的波次）与可选 `dtype`。
3. `allow_multi` 是个**独立标志**，不由 reducer 名推导——`last()` 工厂显式把 `allow_multi` 置为 `False`（`src/kernel/channels.py:80`），所以任何想并发写的键必须在声明处被点名。
4. 节点从不写共享状态，只返回 `Outcome(value, state_delta, ...)`；屏障一次性通过 channel 的 reducer 折叠所有 delta。

两阶段折叠在 `src/kernel/channels.py:122-147`：`WaveWrites.buffer` 攒一整个波次的写入，`check_ambiguous()` 在真正 apply 之前统一校验。

**为什么值得学：** LangGraph 的对应问题是「两个节点并发写同一个 key 时 reducer 覆盖另一份」，`channels.py` 的写法让并发歧义在**编译期就无法构造**——不是「写错了会抛异常」，是「写不出来」。这是 `M9` 的正向形态：故障在正确性层面被消除，而不是在可用性层面被暴露。

**迁移要点：** 已有的 dict 型 state 不需要一次改完。先只给**多写点**的键声明 reducer（通常不超过 5 个），单写点的键维持覆盖语义即可。`allow_multi=False` 这一个标志，性价比高于给所有键加 reducer。

---

## 模式 2 · 未声明通道的写入 = 硬错误，并且指名是谁写的

**服务的不变式：** M9

**做法：** `src/kernel/run.py:224-243` `fold_writes()` 遇到未声明的 key 直接抛 `RuntimeError`，错误信息里带上**节点名和 key 名**。

**为什么值得学：** 「未知 key 静默覆盖」是 M9 最隐蔽的失效方式——它不报错，只是状态悄悄变了一份。报错的关键不在「抛异常」，在**错误信息是否能让作者定位到写的那个节点**。

**迁移要点：** 最小实现是一行断言 + 带上下文的错误信息。不需要重构状态层。

> 注：本案例自己也有例外——声明式门面层 `_FacadeBody` 会为未声明的 key 自动补一个 `last` 通道（`src/runtime/workflow.py:83-85`）。这是**有意**的门面层宽容度，代价是同一套代码在 kernel 层是硬错误、在门面层是静默放宽。审计时若发现两层不一致，要判为 `RISK` 而不是 `OK`：用户从门面层学到的「静默放宽」习惯会带进 kernel 层。

---

## 模式 3 · 两级错误策略：按「谁能修好它」分类，不是按异常类型

**服务的不变式：** M8、L7、L10

**做法：** 库把执行期错误显式分成两级：

- **模型级错误** → 转成数据反馈给模型，让它自己修（`src/runtime/react.py:79-87` 把工具失败写成 `"[tool error]"` 回传；`src/runtime/tools.py:122-126` 缺参数返回 `ToolResult.failure` 而非抛异常；`src/runtime/tools.py:138-139` 工具抛异常也转成 failure）。
- **结构性错误** → Run 直接 fail-fast（`src/kernel/scheduler.py:207-212`）。

配套地，`pyproject.toml` 的 ruff 配置**故意忽略 `BLE001`**（禁止 `except Exception` 的规则），并写明理由：引擎/工具层把异常当数据反馈是本框架的立场。

**为什么值得学：** M8 与 L7 的常见漏点都在**分类依据**上，不是分类缺失。按异常类型分类会错（`ValueError` 有时该重试有时不该）；按「谁有能力修」分类不会错——模型能修的，回给它；模型修不了的，必须让它响亮地死。

**迁移要点：** 这条要**写进代码注释和 lint 配置**，不能只写文档。lint 规则被 CI 强制执行，才算真正的策略；否则下一个贡献者会用 `except Exception: return None` 把失败吞掉。

---

## 模式 4 · `_settle` 区分「完成」和「卡住」，且跳过标记只在不该出现时触发

**服务的不变式：** L1、L7

**做法：** 循环结束有三条**互相区分**的路径（`src/kernel/scheduler.py:173-247` 主循环 + `:364-376` `_settle`）：

1. `is_done()` → 正常完成。
2. 达到 `max_waves` → 硬性熔断，`RUN_FAILED` 并 `break`（`:190-196`）。
3. 一个节点都不 ready 且没人挂起 → 判定为卡住，`_settle` 给出 stalled 结论。

**关键细节：** `sweep_skipped()`（`src/kernel/graph.py:257-279`）只在本波次**没有任何节点 ready** 时才标记 pending 节点为 skipped，并级联到不动点。这意味着它**不会**误杀一条被回边激活的分支——只有「本波次真的没人在跑」时才会收缩。

**为什么值得学：** L1 与 L7 的漏点是「循环停了但不知道为什么停」。三种终态必须**在代码里就是三个不同的分支**，不能靠一个布尔量加事后推断。而 skipped 标记的**触发时机**是这道题最容易做错的地方：无条件调用会把「还在等回边」的节点误判成死节点。

**迁移要点：** 检查已有循环时问一句：结束时的状态能不能区分这三种？如果能，再看 skipped/timeout 类收缩逻辑是不是带前置条件。

---

## 模式 5 · 构建期校验，而不是运行期

**服务的不变式：** L1、X5

**做法：** 五个校验点全在构造/编译期，全部 **fail-closed**（拼错就报错，不静默回落）：

| 校验点 | 位置 | 校验内容 |
|---|---|---|
| 图结构 | `src/kernel/graph.py:119-141` `Plan.validate()` | 节点重名、边引用不存在的节点、entry 无效、join 参数非法 |
| 枚举配置 | `src/kernel/scheduler.py:121-122` | `durability` 只接受白名单值 |
| 枚举配置 | `src/kernel/bus.py:103-104` | `on_full` 只接受 `block` / `drop` |
| 工具注册 | `src/runtime/tools.py:71-72` | 工具重名直接抛 `ValueError` |
| 类型契约 | `src/kernel/types.py:21-35` `_ALLOWED_TRANSITIONS` | 状态机非法转移直接拒绝 |

**为什么值得学：** X5 的漏点是「配置 key 拼错 → 静默回落到无上限默认值」。上表五处全都在拼错的瞬间失败，而且错误信息里带着那个拼错的值。**枚举配置做白名单校验，是消除整类「静默漂移」成本最低的手段。**

**反例（同一个库里的）：** 数字配置没有校验。`concurrency=0` 会让信号量永久锁死、无异常无日志（`src/kernel/scheduler.py:265` 处创建）；`max_waves=-1` 则意味着完全无上限。**枚举校验做得很扎实，数字校验完全没做**——这正是 X5 的真实形态：一个库可以在枚举上满分、在数字上零分。

**迁移要点：** 先只做枚举白名单（成本最低、收益最高），再补数字下限。审计 X5 时应把「枚举配置」和「数字配置」**分开问**，不要合起来判一个 `OK`。

---

## 模式 6 · 审批是 fail-closed，且审批通道不共享

**服务的不变式：** M13、X3

**做法：** 总线三种协议职责分离（`src/kernel/bus.py`）：

| 协议 | 语义 | 故障行为 |
|---|---|---|
| `fire`（`:120-134`） | 观察（side channel） | 订阅者异常被隔离，不影响主流程 |
| `check`（`:136-148`） | 裁定（准入） | **fail-closed**：订阅者报错 → 拒绝放行 |
| `collect` | 收集 | 聚合结果 |

写操作走 `bus.check()`，订阅者异常时返回「不允许」而不是「允许」（`src/runtime/tools.py:128-131`）。

订阅队列**有界**，溢出策略是**具名且可计数**的：`on_full="block"`（反压上游）或 `on_full="drop"`（丢弃并计入 `self.dropped`，`src/kernel/bus.py:54-64`）。

**为什么值得学：** M13 的漏点不是「没有审批」，是**审批的故障方向错了**——观察通道的容错（错误隔离、静默降级）用在准入通道上，就变成「审批服务挂了 = 全部放行」。区分方向只需一条规则：**旁路信息可以容错，准入凭证必须 fail-closed。**

**迁移要点：** 审批与观察必须走不同通道。如果两者复用同一个通道，检查它的异常处理分支：观察端可以 `except: continue`，准入端必须 `except: return deny`。

---

## 模式 7 · 循环控制命令只用两个，且用命名构造区分

**服务的不变式：** L1、L4

**做法：** `src/kernel/command.py:36-71`——控制命令只有 `Goto` 和 `Send`，两个语义用**命名构造方法**表达：

| 构造 | 语义 | 真实用法 |
|---|---|---|
| `Goto.now()`（`:63-66`） | 重新武装**并立即释放** | 回边、跳转、交棒（`src/runtime/react.py:70`） |
| `Goto.rejoin()`（`:68-71`） | 只重新武装，就绪仍由入边和 join 决定 | 迭代收敛点：黑板讨论后的综合（`src/runtime/multiagent.py:156`）、重规划后的综合（`src/runtime/plan_first.py:64`） |

底层是同一个 `immediate: bool`，但库**从不直接暴露这个布尔**。`command.py:39-53` 的 docstring 写明了 `immediate=False` 的理由：迭代收敛点必须等本波次的前驱再次完成，否则会「和它们竞速，在同一波次里读到空结果」。

**为什么值得学：** 这是本库最微妙的一处循环正确性设计，也是 L1/L4 的正向锚点。`immediate=True` 用在收敛点上，模型会读到空黑板然后「合成了一个空结果」——一个不报错的错误。用命名构造而不是布尔，让**误用的成本从「写错一个 bool」变成「编译失败」**（没有第三个名字可用）。

**迁移要点：** 凡是自研 loop 里有「回到某个节点」的操作，都要问：这里需要立即释放，还是需要等入边就绪？两者不同，且必须显式区分。如果只有一个参数，给它起两个名字。

---

## 模式 8 · 深度守卫作为结构性强制（而不是 L3 的答案）

**服务的不变式：** L3

**做法：** `src/kernel/scheduler.py:83-91`——子 Run 启动时检查 `depth >= max_depth`，超限抛异常，注释里写明意图：深度是**结构性上限**，不是可调参数。

**为什么值得学：** 深度守卫是 L3 里做得最好的部分。但**它不是 L3 的答案**——L3 问的是深度**和**广度。本库 `add_instance`（`src/kernel/run.py:207-217`）没有任何实例数上限：一次 `Send` 一万个副本，一万个节点在同一波次 ready，每个都调一次模型。深度有界、广度无界。

**迁移要点：** 补广度的最小改动是给 `add_instance` 加一个实例计数上限，超限抛异常（而不是截断——截断会静默丢任务）：

```python
if len(self.plan.instances_of(node_id)) >= self.max_instances:
    raise RuntimeError(f"instance cap exceeded for {node_id}: {self.max_instances}")
```

审计 L3 时把深度和广度**分别问**，只看到深度守卫就判 `OK` 是最常见的误判。

---

## 模式 9 · 上下文裁剪按原子组，绝不孤立一条工具结果

**服务的不变式：** M9

**做法：** `src/runtime/context.py:99-123` `_tool_groups()` 把消息按「调用 + 其结果」成组，裁剪时**整组丢弃**。注释原文：

> When trimming, drop whole groups, never leaving an orphan tool result with no parent call (that would immediately make the model error out)

`_fit_tail()`（`:126-138`）从尾部反向装填，保证最近的消息完整保留。

**为什么值得学：** M9 的漏点通常是「上下文太大就砍一半」，而砍一半最容易产生的损坏是**孤儿工具结果**——模型看到一个结果却不知道它是谁产生的，直接报错。原子组这一个约束消除了整类损坏。

**迁移要点：** 裁剪逻辑必须先按调用分组，再按组取舍。不需要引入新的数据结构，把消息序列分组即可。

---

## 模式 10 · 压缩分级，机械手段优先于花模型的钱

**服务的不变式：** M9

**做法：** `src/runtime/context.py:79-96` 五级压缩，`_pick_level()`（`:172-179`）按填充率选级，`last_level`（`:164-166`）暴露当前级别供观测：

| 级别 | 手段 | 成本 |
|---|---|---|
| `NONE` | 不处理 | 0 |
| `TOOL_COMPRESS` | 压缩工具输出文本 | 0 |
| `HISTORY_SUMMARY` | 压缩历史消息 | 一次 LLM 调用 |
| `TOPIC_SUMMARY` | 按主题摘要 | 一次 LLM 调用 |
| `EMERGENCY` | 紧急截断 | 0 |

配套的 `_shrink_tool_text()`（`:141-146`）保留头尾，中间写成 `f"...[{n} chars omitted]..."`——**写出省略了多少**，而不是无声截断。`_summary_level()`（`:233-241`）把截断点回退到 `role=="tool"` 消息之外，与模式 9 的原子组一致。

**为什么值得学：** 压缩顺序（机械 → 模型）直接决定了压缩本身的成本，而压缩成本没人算进预算。**「先花免费的手段」这一条应该写进任何上下文管理的设计文档。** 另一个细节是「写出省略了多少」：模型能据此判断是否需要重新调用工具拿全文，无声截断会让它基于残缺信息作答。

**迁移要点：** 压缩必须分级，且级别选择要有可观测输出（`last_level`）。一刀切的固定摘要策略是 `RISK`：要么没到需要压缩的程度就花了钱，要么到了紧急程度还在走慢路径。

---

## 模式 11 · 检查点的 wiring-vs-data 边界

**服务的不变式：** L5

**做法：** `src/kernel/body.py:103-110` `NodeContext` 的 docstring 定义了一条边界：

> Note this is wiring, not data: it holds live objects like the model port and tool port, so it is never serialized. A run's data travels via `input` / `state_delta` and is never smuggled into context — that boundary keeps checkpoints clean.

`shared` 是只读视图（`:131-135`），改变状态只能返回 `state_delta`，由引擎在屏障折叠。

**为什么值得学：** L5 的漏点是「检查点恢复了但状态是脏的」。根因往往是**数据和活对象混在一个对象里**，序列化时要么漏掉字段要么塞进了不该塞的东西。把「连线」（端口、注册表等活对象）和「数据」（`input` / `state_delta`）在类型上分开，检查点就自动变干净了。

**迁移要点：** 审计时看检查点序列化了什么。如果序列化对象里出现了 client、session、connection 这类活对象引用，判 `RISK`。修法不是加白名单，是把两类字段拆到两个类型里。

---

## 模式 12 · 检查点原子写 + 乐观并发

**服务的不变式：** L5、X5

**做法：** `src/backends/file_store.py`：

- `_atomic_write_json()`（`:22-25`）：写临时文件 + `os.replace()`，绝不留下半写文件。
- `FileCheckpointStore.save()`（`:36-51`）：接收 `expected_version`，版本不匹配抛 `RuntimeError`——乐观并发，两个进程同时推进同一个 Run 会立刻被发现。
- `FileEventLog`（`:62-104`）：追加式 `.jsonl`，`after()` 支持增量读。

**为什么值得学：** 崩溃恢复的正确性一半在检查点写得多干净。`os.replace` + 版本校验这两样加起来不到 10 行代码，消除了「检查点文件半写损坏」和「多进程静默覆盖」两类故障。

**迁移要点：** 这两条几乎无迁移成本，任何基于文件的检查点都该有。

---

## 模式 13 · 失败要响亮，且带原始证据（同一个文件里的正反对照）

**服务的不变式：** M4、M7、M10、L7

**做法：** `src/runtime/openai_lite.py:200-209`——流式响应**完全空**时抛异常，而且错误信息里**保留了 3 段原始 SSE 分片**：

```python
raise RuntimeError(
    "empty stream from model"
    f" (last fragments: {raw_fragments[:3]!r})"
)
```

**同一文件里的反例：** `:220-223` 与 `:227-230`，工具调用 JSON 解析失败时 `except json.JSONDecodeError: args = {}`——截断的参数被静默换成空字典，工具随后在空参数上执行。

**为什么值得学：** 一个文件同时示范了「对」和「错」，是最省事的教材。空流报错是响亮的（失败可见、带原始证据、能定位根因）；截断参数是无声的（工具执行了、Run 继续了、结果错了、无人知晓）。**M7 和 M10 的危险从来不在于「出错」，在于「出错后仍然成功返回」。**

**迁移要点：** 审查 `except ... : <默认值>` 这类写法时，问一句：这个默认值让后续代码**继续跑**还是**立刻停**？前者基本总是缺陷。

---

## 模式 14 · 超限要响亮，且绝不合成一个答案

**服务的不变式：** M4、L7

**做法：** `src/kernel/scheduler.py:192-196` 达到 `max_waves` 时 `run.fail()` + 事件 `RUN_FAILED` + `break`。`:378-385` `_final_output()` 只取终止节点的原始输出，**不构造任何东西**。

**为什么值得学：** M4 最危险的形态是「框架帮你把结果补齐了」——smolagents 超限会再调一次 LLM 合成一个看起来完整的最终答案，正常返回、不报错，调用方无从分辨。这里的选择恰好相反：宁可直接失败，不产出任何「看起来完成」的东西。

**迁移要点：** 审计 M4 时不要只看「有没有报错」，要追问：**超限时返回的东西是从哪来的？** 如果是新调一次模型生成的，比静默截断更危险。

---

## 模式 15 · 策略可替换，机制固定

**服务的不变式：** M11、L8

**做法：** 三个策略都是 `Protocol`，kernel 只依赖 Protocol，从不 import 具体实现：

| 策略 | 定义 | 默认实现 | 替换实现 |
|---|---|---|---|
| 上下文管理 | `src/runtime/context.py:24-25` `ContextManager` | `WindowContext`（`:28-39`）、`SummarizingContext`（`:42-65`） | `TieredCompactionContext`（`:149-241`） |
| 记忆 | `src/runtime/memory.py:32-36` `Memory` | 关键词重叠召回 | 任意实现 |
| 技能 | `src/runtime/skills.py:32-84` `SkillRegistry` | SKILL.md frontmatter 解析 | 任意实现 |

`RetryPolicy`（`src/kernel/graph.py:37-63`）的 `retry_on` 接受**元组或谓词**，`matches()`（`:60-63`）统一分发。

**为什么值得学：** M11 问「可重试 vs 不可重试是否分类」，L8 问「退避与参数变更」——这两个不变式的答案都取决于**分类能不能被替换**。把判定逻辑做成策略对象，用户就能替换分类而不改引擎；写成硬编码 if-else，用户只能改引擎。

**反例（同一个库里的）：** `retry_on` 的**默认值**是 `(Exception,)`——过宽，会把程序 bug 也重试掉。`backoff` 是纯指数（`src/kernel/graph.py:55-58`，`base_delay * factor ** failed_attempt`），**无 jitter、无最大延迟上限**。策略机制做得好，默认值做得糙。

**迁移要点：** 分类逻辑做成可替换对象，同时**默认值要保守**。`retry_on=(Exception,)` 这个默认值意味着「重试策略」实际变成了「所有错误都重试」，L8 的漏点就回来了。

---

## 模式 16 · 幂等键的设计要点（含本库自身的反例）

**服务的不变式：** L6

**做法：** `src/kernel/types.py:60-68` 明确把 `call_id` 文档化为幂等键。

**本库的缺陷（三个，全部 `[源码确认]`）：**

1. **键会碰撞。** `src/kernel/body.py:178-180`：
   ```python
   call_id = f"{self.run_id}:{self.node_id}:{attempt}"
   ```
   `attempt` 是节点级计数，不是调用级计数。`src/runtime/react.py:81` 在同一个节点的同一个 attempt 里循环执行多个待处理工具调用——**多个调用拿到同一个 `call_id`**。
2. **没有强制去重。** `src/runtime/tools.py:117-139` `dispatch()` 从不停下来读 `call_id`。
3. **跨恢复会漂移。** `Goto` 重新武装会把 `attempts` 加一，所以恢复后的重试拿到的是新键——同一逻辑操作，恢复前后键不同。

**为什么值得学：** 这条是本文件里唯一以**缺陷**入册的模式，因为它示范了一个比「没有幂等」更糟的状态：**幂等键存在、被文档化了、但会碰撞、且无人强制**。错误的去重比没有去重更危险——它会让操作**静默丢失**，而没有人报错。

**修复方向（最小改动）：** 键里加上调用内容摘要，使其在碰撞场景下依然唯一：

```python
digest = hashlib.sha1(
    f"{name}|{json.dumps(arguments, sort_keys=True)}".encode()
).hexdigest()[:12]
call_id = f"{run_id}:{node_id}:{attempt}:{digest}"
```

**迁移要点：** 审计 L6 时按三步问：(1) 有没有键？(2) **键的粒度是操作级还是更粗？** (3) 谁在读这个键、有没有强制？三步都答不上就是缺陷，即使键本身存在。

---

## 模式 17 · 审批分类要覆盖所有路径（分类存在 ≠ 覆盖完整）

**服务的不变式：** M13

**做法：** `side_effect: read | write` 分类 + 写操作过审批闸，是一条**真实的**权限边界（`src/runtime/tools.py:58` 字段定义、`:128-131` 闸口、`src/kernel/bus.py:136-148` fail-closed 裁定）。

**但存在三条旁路：**

| 旁路 | 位置 | 后果 |
|---|---|---|
| 委派被标成只读 | `src/runtime/multiagent.py:89`、`src/runtime/agent.py:121-130`（`:128`） | 委派一个 agent 去做写操作，绕过了写审批 |
| 所有 MCP 工具硬编码只读 | `src/runtime/mcp.py:60-82`（`:78` 写死 `side_effect="read"`） | 外部工具无论实际做什么，都不过闸 |
| 无注入/PII 处理 | 库内无相关代码 | M13 的另外两个维度完全未覆盖 |

**为什么值得学：** M13 最常见的误判是「有分类就算有护栏」。这里分类存在、闸口存在、fail-closed 也存在，但**分类只覆盖了本地注册的工具**——旁路全部走的是「分类默认值」。

**迁移要点：** 审计 M13 时问三句：(1) 分类有哪些取值？(2) **哪些代码路径会跳过分类、走默认值？** (3) 默认值在权限模型里代表什么？只要有任何路径走默认值，判 `RISK` 而非 `OK`。

---

## 案例 2 · deepseek-harness（2026-09-07 上游 `master` 源码核验）

来源：[github.com/deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。架构是「一切皆插件」：agent loop 本身是插件，没有特权核心。下面每条做法都标了上游路径，行号随 `master` 漂移，引做法不引行号。

## 模式 18 · 硬预算缺失时，把「谁负责补」写进自文档

**服务的不变式：** M1、L2

**做法：** `packages/core/agent-loop/src/constants.ts` 整个文件 202 字节，只有一个常量：

```ts
/** Default maximum in-flight parallel-safe calls per agent step. */
export const DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10
```

**没有任何 max-step、max-turn、iteration 常量。** agent-loop 的主循环是 `while (true)`。项目对此的自文档：

> **No built-in turn budget** — tool calls or steering continue the current turn; a policy that bounds runaway turns must cancel from an existing lifecycle extension point such as `agent/turn-stopping`.

限轮的机制确实存在——`agent/turn-stopping` 事件——但 `docs/event-producer-consumer.md` 显示它**只被 `hooks-claude-code` 与 `hooks-codex` 两个钩子桥消费，没有内建的安全策略消费它**。

**为什么值得学：** 这不是「没做」，是**把责任显式外包并写明接手点**。比它差的两种形态：(a) 以为有内建预算其实没有，且无人知晓；(b) 计数器存在但没人把它当限用（案例 1 的 M1 就是这个形态）。判 `GAP` 时要区分「缺失且无人知晓」与「缺失但被显式文档化并指到扩展点」——后者的修复成本已知，判 `RISK`；前者判 `GAP`。

**迁移要点：** 如果决定把预算外包给上层策略，必须留下两样东西：扩展点的名字，以及「目前谁在消费它」。少了第二样，这条文档就是一句空话。

---

## 模式 19 · 重试的完整正向形态（案例 1 模式 15 的反例在这里有解）

**服务的不变式：** L8、L7、X1

**做法：** `packages/llm/llm/src/retry-policy.ts` 与 `packages/llm/llm-retry/src/index.ts`。省略 `retryPolicy` 即得到默认策略，五项要素全部到位：

| 要素 | 默认值 | 位置 |
|---|---|---|
| 最大重试 | 5 | `retry-policy.ts:14` |
| 可重试错误码白名单 | `EMPTY_RESPONSE`、`RATE_LIMIT`、`SERVER`、`TIMEOUT`、`TRANSPORT` | `:18-24` |
| 初始延迟 | 500 ms | `:15` |
| 最大延迟 | 10 000 ms | `:16` |
| **jitter 比例** | **0.1** | `:17` |

jitter 实现是**对称**的，且夹在 `[0, 2]` 内：

```ts
const exponential = Math.min(config.initialDelayMs * 2 ** exponent, config.maxDelayMs)
const jitter = 1 - config.jitterRatio + 2 * config.jitterRatio * random()
return Math.min(exponential * jitter, config.maxDelayMs)
```

指数溢出也被挡住：`Math.min(retry - 1, 1024)`。另外 `Retry-After` 处理有方向性——normal 模式下 provider 的延迟超过本地上限时**放弃重试**（交给下游），而不是硬扛。

**为什么值得学：** 案例 1 模式 15 的反例是「`retry_on=(Exception,)` 默认值过宽 + 纯指数无 jitter、无最大延迟」。这里三项全有解：**白名单收窄了重试范围**（`retryableCodes` 不能为空、不能重复，都 fail-loud），**对称 jitter 防 thundering herd**，**双层上限**（`maxDelayMs` 夹本地延迟，也夹接受的 provider 延迟）。重试判据也是策略对象（provider 各自持有 `retryPolicy`），满足模式 15 的可替换性要求。

**迁移要点：** 检查重试代码时按五行问，缺一行都不能判 `OK`：有没有最大次数？可重试错误码是不是白名单？延迟有没有上限？有没有 jitter？jitter 是乘性还是加性？案例 1 的策略机制满分、默认值零分；案例 2 五项齐全——**机制和默认值必须一起判**。

---

## 模式 20 · 重试状态必须落在持久事件里，取消时日志仍然一致

**服务的不变式：** L5、X2、M1

**做法：** 重试调度是**先写日志再等待**：

```ts
agent.session.append('llm/retry', eventData)      // 调度即落盘，携带 delayMs、failure、policyKey
if (!await cancellableDelay(delayMs, fusedSignal)) return
agent.session.append('llm/retry-started', { retryId, turn, step, retry })
return { kind: 'retry' }
```

配合两处结构性强制：等待用 `AbortSignal.any([signal, lifetime.signal])` 可取消；计数由**会话投影**按 `(provider, policyKey)` 键控——同一 step 里换策略会产生不同键，重试计数不会跨策略串号。

**为什么值得学：** L5 的漏点是「恢复会重放已完成的副作用」。这里的关键不是「记了日志」，是**日志在等待之前**——背压期间被取消，留下的是「已调度、未开始」的可重建状态，而不是一个悬空的重试。X2 的可重放也同时满足：`failure` 与 `retryId` 都在事件里，重试链可从会话日志重建。

**迁移要点：** 审计重试/等待类逻辑时问：等待发生前有没有可持久化的记录？回答「记录在等待之后」的，恢复语义是坏的。

---

## 模式 21 · 不变量校验是独立插件，且校验整个会话历史

**服务的不变式：** L5、X5

**做法：** 重试插件旁边有一个 `invariant.ts` 伴生插件，用 Cordis 的 invariant 服务做校验。它不是「校验新写入的那一条」，而是**校验整个历史**：

```ts
for (const session of ctx.sessions.list()) validateSession(session, fail)   // 已加载的历史
ctx.on('session/created', (s) => { validateSession(s, fail) }, { global: true })  // 新建会话
ctx.on('internal/dispatch', (_m, name, args) => {
  if (name !== 'session/event') return
  const [session, event] = args
  if (event.type === 'llm/retry') validateRetry(session.snapshotEvents(), fail)  // 每次追加
})
```

而且校验的是**跨记录的一致性**，不是单条字段的形状：`retry` 必须等于同 `(step, provider, policyKey)` 链上上一条记录 +1；`retryId` 必须跨链保持；`turn` 必须等于当前打开的 turn（用 `findLast` 找 turn 边界确认）；provider 必须与失败请求实际路由的 provider 一致。

**为什么值得学：** X5 的模式 5 讲的是「拼错的配置立即抛」。这里更进一步：**配置拼错只破坏当下，事件语义错乱会污染历史**。只校验「新写入的那一条」无法发现「第 3 条重试声称自己是第 1 条」。校验整个历史才能发现序列错乱——这是事件溯源系统的正确性所在，也正好对应 X2 判 `OK` 的前提：可重放要求历史本身自洽。

**迁移要点：** 有事件日志的系统，问一句：校验是逐条还是整序列？逐条的，序号错乱、ID 复用、跨记录矛盾全部看不见。

---

## 模式 22 · 重试与 agent loop 完全解耦，「直接调用」的消费者是单次的

**服务的不变式：** M2、L1

**做法：** 重试逻辑不在 loop 里，挂在 loop 暴露的 `agent/request-error` 扩展点上：

```ts
const disposeListener = ctx.on('agent/request-error', (payload, next) =>
  track(recover(payload, next)),
)
```

结果是明确的：**直接调 `ctx.llm.stream()` 的消费者是单次尝试**，因为没有 loop、没有扩展点、没有重试。README 把这一点写成选择理由——原始 stream 无法持久区分「已发出的 chunk」，所以不该伪装成可重试。

**为什么值得学：** 这正面回答了 M2「检查点在循环内部还是只查一次」——重试不是循环的一部分，而是循环的扩展点，边界由框架定义而不是由调用者约定。**边界可发现**：想重试就必须经过 loop，绕过去就没有，不存在「我以为会自动重试但其实没有」。

**迁移要点：** 审计重试覆盖范围时问：哪些代码路径**不在**重试保护内？如果答案需要读多个文件才能推出来，这就是 M2 的缺口形态——边界不可发现比边界不存在更危险。

---

## 模式 23 · 裁剪预算有跨字段不变量，构造期校验

**服务的不变式：** X5、M9

**做法：** `packages/compaction/compaction-tool-result-pruner/src/config.ts` 除了逐字段校验，还有一条**跨字段**不变量：

```ts
const emittedChars = resolved.headChars
  + codePointLength(PRUNE_MARKER)
  + resolved.tailChars
if (emittedChars > resolved.thresholdChars) {
  throw new Error(
    `ToolResultPruneConfig: headChars + marker + tailChars (${emittedChars}) `
    + `must be at most thresholdChars (${resolved.thresholdChars})`,
  )
}
```

配置对象最后 `deepFreeze(structuredClone(resolved))`——**深层不可变**。另外两个细节：字符计量用 Unicode code point（`Array.from(text).length`），不拆代理对；省略标记 `'\n\n[... tool result middle pruned ...]\n\n'` 是具名常量，且执行期还有兜底断言 `throw new Error('tool-result prune: replacement must be smaller and within threshold')`。

**为什么值得学：** 模式 5 与模式 24 讲的是逐字段校验（拼错即抛）。这里补的是第三类：**字段各自合法、合起来不合法**。`headChars=5000, tailChars=4000, thresholdChars=8192` 三个值全都合法，但加起来超过阈值，裁剪器会进入死循环或输出比原文更长。**跨字段不变量必须在构造期校验**——留给运行期，就是模式 13 说的「出错后仍然成功返回」。

**迁移要点：** 审计配置校验时问三类：拼错会不会抛？越界会不会抛？**字段之间有没有关系需要校验？** 多数项目答得上前两问、答不上第三问。

---

## 模式 24 · 反例入册（案例 2 的三个）

**服务的不变式：** M13、X5、M1

### 反例 A · 建议性护栏被当硬边界

**证据：** `packages/guard/timeout-policy/src/index.ts` 的自文档：

> **Cooperative, never a hard kill** — the deadline only notifies via `exec.signal`; a tool that ignores the signal does not stop on timeout, the wrapper remains inside `await next()`…
> **No blanket budget** — only tools that declare `timeoutMs` on their `ToolDefinition` get a deadline; undeclared tools (the shipped `bash`, `read`, `write`, and `edit` declare none) have no registry-wide default.

`repeat-tool-reminder` 同样自述「Advisory, not veto. The guard enriches post-execute decisions with model context; it never blocks or rewrites a call.」

**可迁移的判据：** 同一个仓库里有一个**独立的 fail-closed 强制层**（`packages/interaction/user-approval/`，描述为「Asks composed answerers for one-shot allow/reject decisions and **fails closed without one**」）。也就是说：建议性约束降低失败概率，**但不让失败不可能**。判 M13 时要分清：这条防线能不能物理阻止被禁止的动作？只能提醒、不能阻止的，不是安全边界——唯一的边界如果是建议性的，判 `RISK`。

**注意：** 这两个 guard 都**默认不启用**（内建 `bash`/`read`/`write`/`edit` 均未声明 `timeoutMs`），所以这不是「边界失效」，是「边界需要显式配置且无注册表默认值」。审计时两个问题都要问：有没有强制层？默认配置下它启没启用？

### 反例 B · 省略即重置（配置合并语义）

**证据：** `packages/llm/llm-retry/src/index.ts:30-37`——插件级配置**不接受任何 key**，连 `retryPolicy` 都被明确拒绝：

```ts
const [key] = Object.keys(config)
if (key === 'retryPolicy') {
  throw new Error('llm-retry: retryPolicy belongs under each provider configuration')
}
throw new Error(`llm-retry: unknown key "${key}"`)
```

策略完全归 provider 配置所有。这个方向的漂移是**静默的**：patch 作者是给某一路由**加**限制（写 `maxRetries: 1`）还是**移除**限制（漏写 `retryPolicy`），从最终配置上看不出来——漏写会拿到 normal 默认（5 次重试），而 `always` 模式的 provider 拿到的是**无重试上限**。`retry-policy.ts:165-166` 用的是 `??` 语义：省略即取默认，不是继承上一版本。

**可迁移的判据：** 审计配置层时问：**这个配置是合并还是整体替换？** 整体替换时，省略一个字段得到的是默认值而不是旧值；如果默认值恰好是无上限，patch 会静默移除安全限制，而配置审计看不出来。模式 22 的「边界不可发现」在配置层的对应形态就是它。

### 反例 C · `always` 模式是无上限重试，但边界条件写得清楚

**证据：** `packages/llm/llm-retry/src/index.ts:199-214`：

```ts
if (policy.mode === 'always') {
  if (signal.aborted || lifetime.signal.aborted) return
  const downstream = await settleDownstream(next)
  if (downstream.type === 'error') {
    ctx.logger.warn('llm-retry: provider "%s" always policy ignored a downstream recovery failure: %o', ...)
  }
  if (downstream.type === 'decision' && downstream.decision?.kind === 'retry') return downstream.decision
}
```

`invariant.ts:73-75` 则把它变成结构性约束：`always` 模式**必须**没有 `maxRetries`。

**为什么记成反例：** 这是 L10 的「挂起等人工」缺口的另一种形态——重试耗尽只有两条路（抛异常、或无上限重试），没有第三档。项目把这个选择**做成类型上的强制**（`always` 不能带 `maxRetries`）比含糊更诚实，但判 L10 时仍要判 `RISK`：无上限重试在 provider 持续故障下就是 M1 的无预算，只是换了个名字。

---

## 案例 3 · pi（2026-09-08 上游 `main` 源码核验）

来源：[github.com/earendil-works/pi](https://github.com/earendil-works/pi)。agent loop 在 `packages/agent/src/`，重试与恢复拆在 `packages/ai/src/utils/retry.ts` 与 `packages/agent/src/harness/runtime/drive/`。这是全库注释最诚实的一个案例——多处注释直接写出「这一行是为了挡哪种具体的坏情况」。

## 模式 25 · 被截断的工具调用整批判失败，一条都不执行

**服务的不变式：** M7、M10

**做法：** `packages/agent/src/agent-loop.ts`：

```ts
// A "length" stop means the output was cut off by the token limit, so
// every tool call in the message may carry truncated arguments. Fail
// them all instead of executing potentially borked calls.
const executedToolBatch =
	message.stopReason === "length"
		? await failToolCallsFromTruncatedMessage(toolCalls, emit)
		: await executeToolCalls(currentContext, message, config, signal, emit);
```

`failToolCallsFromTruncatedMessage` 的注释把原因写成了本案例最有信息量的一句：

> Streamed tool-call arguments are finalized with a best-effort JSON salvage parser, so a truncated message can yield tool calls whose arguments parse and validate but are silently incomplete. None of them are safe to execute; report each as an error so the model can re-issue them.

**为什么值得学：** 案例 1 模式 13 记录的反例是「截断的参数被静默换成空字典，工具照跑」。pi 面对的是同一个问题，但更难——**它有一个 best-effort 抢救解析器，所以截断的参数能通过 schema 校验**。校验层全绿，语义层全错。这种情况下单点修复（校验参数、检查 `stop_reason`）都不够：**只有 `stop_reason` 一个信号能把「校验通过的截断调用」和「真的完整的调用」区分开**，所以判据要落在 stop_reason 上，而且要**整批失败**——同一消息里的调用共享同一个截断点，单独判断其中一条是伪精细。

**迁移要点：** 审计 M7 时按三档问：(1) 有没有「解析失败就换默认值」的分支（案例 1 的形态）？(2) 有没有抢救式解析器把残缺输入变成合法输入（pi 的形态，更难发现）？(3) 截断信号（`stop_reason: length` / `finish_reason: length`）参与决策了吗？第 (2) 档答「有」而第 (3) 档答「没有」，就是 M7 的最坏形态——防线全部通过，但拦不住任何东西。

---

## 模式 26 · 批终止是跨字段不变量，全批一致才终止

**服务的不变式：** M11、L7

**做法：** `packages/agent/src/agent-loop.ts`：

```ts
function shouldTerminateToolBatch(finalizedCalls: FinalizedToolCallOutcome[]): boolean {
	return finalizedCalls.length > 0 && finalizedCalls.every((finalized) => finalized.result.terminate === true);
}
```

**为什么值得学：** 一个模型消息可以同时发出多个工具调用，其中一部分标了终止、一部分没有。三种处理方式的后果不同：**少数服从多数** → 少数派想终止却终止不了；**任一条终止就终止** → 想继续的调用被截断且结果未回收；**全批一致才终止** → 分歧时保守地继续，让模型自己调和。这里选的是第三种，而且用 `every()` 写成了不可歧义的表达式。配套的一处：未知工具名返回错误结果而不是抛异常（`createErrorToolResult(\`Tool ${toolCall.name} not found\`)`，`isError: true`）——**工具幻觉走 M5 的自愈档，不是崩溃档**。

**迁移要点：** 凡是「一批动作里有多个布尔决策」的地方（终止、跳过、重试、回滚），都要问一句：分歧时按哪个来？答案必须写进代码而不是靠约定。`every()` / `some()` 选错一个，就是静默丢掉少数派的意图。

---

## 模式 27 · 可重试错误用双层正则，非重试的测在前

**服务的不变式：** L8、M11

**做法：** `packages/agent/src/harness/runtime/drive/retry.ts`——「classifier and the policy-driven retry loop live together and stay reusable」，分两层，**非重试的在前**：

```ts
const NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN = buildProviderErrorPattern([
	"GoUsageLimitError", "FreeUsageLimitError",
	"Monthly usage limit reached", "available balance",
	"insufficient_quota", "out of budget", "quota exceeded", "billing",
]);
// 然后再测 RETRYABLE：overloaded / rate.?limit / 429 / 500 / 502 / 503 / 504 / 524 /
// service.?unavailable / connection.?refused / getaddrinfo / EAI_AGAIN / socket hang up /
// timed? out / stream ended before message_stop / ResourceExhausted / ...

export function isRetryableAssistantError(message: AssistantMessage): boolean {
	if (message.stopReason !== "error" || !message.errorMessage) return false;
	const errorMessage = message.errorMessage;
	if (NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN.test(errorMessage)) return false;
	return RETRYABLE_PROVIDER_ERROR_PATTERN.test(errorMessage);
}
```

**为什么值得学：** 案例 2 模式 19 给出的是白名单形态（枚举错误码），案例 1 的反例是 `retry_on=(Exception,)` 默认过宽。这里补的是**白名单仍然不够**的那种情况：`"quota exceeded"` 和 `"overloaded"` 都是 provider 报错，一个该重试一个不该——两者文本上无法用单个白名单区分。解法是**双层判定，且非重试优先短路**：如果先测可重试集合，`"quota exceeded"` 会因为匹配别的分支而被误重试，白烧一次请求才发现是配额问题。顺序错了，白名单就是装饰。

另一处值得记的：**中断（abort）是终态，永不重试，也不当成功上报**。退避睡眠期间被中断会归一化成 provider 流中断的同一消息形状（`stopReason: "aborted"`，拆掉 `errorMessage`），这样调用方只有一种「被取消」的形状要处理。

**迁移要点：** 审计 L8 时问第五问的下一层：**你的白名单里有没有互相重叠的模式，重叠时的优先级是显式的吗？** 文本匹配型分类（正则、字符串包含）天然有重叠，重叠时必须显式声明「哪个先测」。异常类型型分类不会有这个问题——这也是模式 15「分类可替换」的收益之一。

---

## 模式 28 · 指数退避要防溢出，且防的是两层上限

**服务的不变式：** L8

**做法：** `packages/ai/src/utils/retry.ts`——这是**第二个独立来源**，与案例 2 的 `Math.min(retry - 1, 1024)` 互证：

```ts
export function retryDelay(baseDelayMs: number, attempt: number): number {
	const delay = baseDelayMs * 2 ** Math.max(0, attempt - 1);
	return Number.isSafeInteger(delay) ? delay : Number.MAX_SAFE_INTEGER;
}
export function retryNotBefore(baseDelayMs: number, attempt: number, now = Date.now()): number {
	const sum = now + retryDelay(baseDelayMs, attempt);
	return Number.isSafeInteger(sum) ? sum : Number.MAX_SAFE_INTEGER;
}
```

`waitUntil` 里还有一处：`setTimeout(check, Math.min(remaining, 2_147_483_647))`——**setTimeout 的 32 位上限**，和 JS 安全整数的上限是两件事。

**为什么值得学：** 「`2 ** n` 溢出」听起来像理论问题，但两处上限的性质完全不同：`Number.MAX_SAFE_INTEGER` 挡住的是精度丢失（延迟变成一个看起来合理实则不可靠的大数），`2_147_483_647` 挡住的是 timer 语义错误（`setTimeout` 收到超过 32 位的值会被截断成一个小值，**退避变成即时重试**）。第二处尤其反直觉——不是「等太久」，是「等不到」。

**迁移要点：** 审计指数退避时按两处问：(1) 延迟计算有没有溢出防护？(2) **把它交给谁计时？那个 API 有自己的上限吗？** `setTimeout` / `setInterval` 的 32 位上限、`time.Sleep` 的 int 单位，都在这一类里。

---

## 模式 29 · 检查点是操作状态转移，不含活对象；边界处显式记录「续跑什么」

**服务的不变式：** L5、X2

**做法：** `packages/agent/src/harness/runtime/drive/checkpoint.ts`——检查点值是纯数据类型的状态转移，写操作是一个 `commit` 事务（`writes` + `operationState` + `materialize`），而不是一个包含 client / stream 的快照对象：

```ts
const nextState: CheckpointOperation = {
	...operationScopeOf(current),
	at: "checkpoint",
	continuation: { kind: "need_assistant", overflowRecoveryUsed: false },
	triggerEntryId,
};
```

压缩（compaction）发生在边界上，边界记录**精确的续跑点**：

```ts
kind: "resume_checkpoint",
resumeAfter: { continuation: current.continuation, triggerEntryId: current.triggerEntryId },
```

**为什么值得学：** 案例 1 模式 11 讲的是 wiring-vs-data 的**类型分离**（让检查点自动变干净）。这里补的是另一半：**分离了类型还不够，恢复时要知道「从哪个动作的哪一步续」**。`continuation` 是个枚举 + 标志（`kind: "need_assistant"`、`overflowRecoveryUsed: false`），不是自由文本也不是一个函数。恢复语义由状态机的 `at` 字段决定，不靠重放历史去推断——推断出来的恢复点，恢复一次就变一次。

**迁移要点：** 审计 L5 时问两步：(1) 检查点里有没有活对象（案例 1 的问法）？(2) **有没有一个「下一步是什么」的枚举状态？** 只有第 (1) 步答得上而第 (2) 步答不上，恢复会重新跑完整动作，副作用重放。

---

## 模式 30 · 被中断的生成不重新调用模型，用已提交的片段收尾

**服务的不变式：** L6、L7、X2

**做法：** `packages/agent/src/harness/runtime/drive/terminal.ts`——中断不触发第二次 provider 调用，而是把已经流出来的部分包成一个明确的错误消息：

```ts
function interruptedAssistantMessage(identity, partial): SettledAssistantMessage {
	const warning =
		"Assistant request was interrupted. The preceding content is the latest committed partial; newer live output may be missing and the external outcome is unknown.";
	return partial === undefined ? { ... usage: ZERO_USAGE, stopReason: "error", errorMessage: warning }
		: { ...partial, usage: ZERO_USAGE, stopReason: "error", errorMessage: warning };
}
```

恢复出来的事件带 `recovery: true` 标记，与真实模型输出可区分。

**为什么值得学：** 这是 L6「副作用幂等」和 M4「超限行为」的交集，也是全库少见的诚实写法。注释里明说了**外部结果未知**——请求已经发出去了，模型可能已经生成了后半段并触发了副作用，但客户端看不到。这种情况只有两种处理：**重调模型**（幂等破坏：可能重复副作用）或**收尾并声明不确定**（正确但不完美）。这里选了后者，而且把不确定性写进了返回的错误信息里，调用方不会误以为「这是模型的最终答案」。

`recovery: true` 标记对应一个之前没有的判据：**合成的、恢复的输出必须在事件流里可区分**。没有这个标记，事件溯源看起来完整，但重放时无法分辨哪段是模型说的、哪段是框架补的——X2 的「能重建运行」在这一点上直接失效。

**迁移要点：** 审计恢复逻辑时问：恢复产生的是「新的一次模型调用」还是「对已有输出的标注」？前者要幂等键保护，后者不需要但**必须在事件里打标**。两者都不做的，是 X2 与 L6 的复合缺陷。

---

## 模式 31 · 跨字段不变量在构造期抛出；清理只删当前状态标为 pending 的部分

**服务的不变式：** X5、L5、M9

**做法一：构造期跨字段不变量**（`packages/agent/src/harness/runtime/drive/recovery.ts`）——与案例 2 模式 23 互证，是第二个独立来源：

```ts
if ((status === "failed") !== (error !== undefined)) {
	throw new SessionInvariantError("Only a failed operation result may carry an error");
}
```

`failed` 却无 error、或成功却带 error，都在构造时炸，不留到运行期。

**做法二：状态依赖的清理**（同文件）——不是「无条件清理所有 scratch」，而是先查当前状态标记了什么 pending，再按前缀扫出属于本操作的 scratch，只删那部分：

```ts
let frameDelete: Write | undefined;
if (state.at === "assistant.effect_pending" || state.at === "deferred.effect_pending") {
	frameDelete = deleteList(pendingAssistantFrames(operationId, state.responseEntryId));
}
```

**做法三：并发写入的序列化键用规范路径**（`packages/agent/src/harness/tools/file-mutation-queue.py` 对应的 `file-mutation-queue.ts`）——不是模型给的字符串路径，而是 symlink 解析后的规范路径：

```ts
const absolutePath = getOrThrow(await env.absolutePath(path, context));
const canonicalPath = await env.canonicalPath(absolutePath, context);
if (canonicalPath.ok) return canonicalPath.value;
if (canonicalPath.error.code === "not_found" || canonicalPath.error.code === "not_supported") return absolutePath;
```

队列状态装在 `WeakMap<ExecutionEnv, MutationQueueState>` 上（生命周期有界），清理只删队尾且先比对同一对象（`if (state.queues.get(key) === chainedQueue) state.queues.delete(key)`）。

**为什么值得学：** 三条合起来是「不变量写在哪一层」的完整谱系：**构造期**（数据形状矛盾立即炸）→ **运行期按状态裁剪**（清理范围由当前状态决定，无条件清理会删掉在途数据）→ **序列化键规范化**（`a/b.ts` 与 `./a/b.ts` 指向同一文件但字符串不同，用字符串做键会让两个写者各自拿锁）。第三条是 M9 的一个具体形态：并发控制失效不总是「两个节点写同一个 key」，也可以是**「两个字符串不同的写者其实写同一个文件」**。

**迁移要点：** 审计 X5 时把构造期不变量按三处问：数据形状之间（案例 2 模式 23）、状态与字段之间（这里）、路径/标识规范化之间（这里）。审计并发控制时问：锁的键是**规范化的标识**还是**调用方给的字符串**？后者在 symlink、相对路径、大小写差异下都会漏。

---

## 案例 4 · OpenHarness（2026-09-08 上游 `main` 源码核验）

来源：[github.com/HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)。loop 在 `src/openharness/engine/query.py`，外层编排器在 `src/openharness/autopilot/service.py`。

## 模式 32 · 无上限的退出路径有它自己的终态错误

**服务的不变式：** L1、M4

**做法：** `src/openharness/engine/query.py`——循环条件允许无限循环，但两条退出路径给出**不同的错误**：

```py
if context.max_turns is not None:
    raise MaxTurnsExceeded(context.max_turns)
raise RuntimeError("Query loop exited without a max_turns limit or final response")
```

配套的三处「不许静默」：

```py
if final_message is None:
    raise RuntimeError("Model stream finished without a final message")

if final_message.role == "assistant" and final_message.is_effectively_empty():
    yield ErrorEvent(message=(
        "Model returned an empty assistant message. "
        "The turn was ignored to keep the session healthy.")), usage
    return
```

**为什么值得学：** 这是 L1 的「三条终态」里此前缺的那一条。案例 1 模式 4 的三条是「正常完成 / 达上限熔断 / 无人 ready 判为卡住」——**都没覆盖「根本没设上限」**。一个 `while` 循环在理论上永远可能不退出，所以「未达上限就退出」本身是异常事件，必须有它自己的名字：是达上限了（配置问题，降轮数），还是压根没配上限（配置缺失，M1 的 GAP）？混在一个 `RuntimeError` 里，报告的修复建议就没法定位。

空 assistant 消息的处理是同一个立场的另一个应用：**不把它 append 进历史然后继续跑**（那会让下一轮看到一个无内容的 assistant 消息，多数 provider 直接报错），而是响亮地终止整个会话并说明原因。

**迁移要点：** 审计 L1 时问第四问：**如果这个循环根本没有上限，退出时能被观测到吗？** 答「不能」的不是 L1 的缺口，是 L1 和 M1 的复合——因为没有上限，所以「退出」这件事不会自动成为异常。

---

## 模式 33 · 框架自己引起的重试不占用调用方的预算

**服务的不变式：** M1、M2、L8

**做法：** `src/openharness/engine/query.py`——模型拒绝本地上限、框架改用 provider 上限重试时，把轮数退回去：

```py
if _is_completion_token_limit_error(exc):
    supported_limit = _extract_completion_token_limit(exc)
    if supported_limit is not None and effective_max_tokens > supported_limit:
        previous_max_tokens = effective_max_tokens
        effective_max_tokens = supported_limit
        yield StatusEvent(message=(
            f"Model rejected max_tokens={previous_max_tokens}; "
            f"retrying with provider limit {effective_max_tokens}.")), None
        turn_count = max(0, turn_count - 1)
        continue
```

`max(0, ...)` 保证不会退成负数。

**为什么值得学：** 一个此前没被单独写出的判据。预算的语义取决于**谁让它多跑了一轮**：模型说「继续」多跑一轮，是模型的选择，该算；框架因为自己配错了 `max_tokens` 多跑一轮，是框架的错，不该算到调用方头上。不区分这两者，调用方会看到「预算用完了」而实际原因是一开始的上限配置就错了——报告的修复建议会指向错误的地方（降轮数，而不是修正限）。

**迁移要点：** 审计 M1/M2 时问：**这个计数器在自纠类重试里被扣减、回退，还是不动？** 三种答案的语义不同，必须显式。回退要带上界（`max(0, ...)`），否则自纠可以无限续命。

---

## 模式 34 · 钳制必须可观测，且枚举值与数字值分开钳

**服务的不变式：** X5、M6

**做法一：非对称钳制**（`src/openharness/engine/query_engine.py`）——`None` 是合法语义，数字有下限：

```py
def __init__(self, ..., max_turns: int | None = 8) -> None:
def set_max_turns(self, max_turns: int | None) -> None:
    self._max_turns = None if max_turns is None else max(1, int(max_turns))
```

`max(1, ...)` 挡住 `max_turns=0` 把循环静默变成「永不进入」，而 `None`（无上限）仍然是一个可表达的合法状态。两者语义相反，必须分开处理。

**做法二：钳制必须被报告**（`_safe_completion_token_cap`）——钳到 `MAX_SAFE_COMPLETION_TOKENS` 后发一条 `StatusEvent`，把请求值和实际值都写出来。

**做法三：工具结果按提交顺序发出**（`query.py`），理由是代码注释里写清楚的：

```py
# Use return_exceptions=True so a single failing tool does not abandon
# its siblings as cancelled coroutines and leave the conversation with
# un-replied tool_use blocks (Anthropic's API rejects the next request
# on the session if any tool_use is missing a matching tool_result).
```

这是「孤儿工具结果」这条判据的**第二个独立来源**（案例 1 模式 9 是裁剪侧，这里是并发执行侧）。

**为什么值得学：** X5 的判据此前只覆盖「配置错了会不会抛」。这里补两条：**钳制之后要可观测**（静默钳制等于调用方以为配了 8192、实际跑 4096，成本核算和超时预测全错），以及**枚举型 `None` 与数字型边界要分开**（`max(1, None)` 在 Python 里会抛 `TypeError`，在 TypeScript 里会 `NaN` 传播——同一条「数字要有下限」的规则套在可空类型上就是 bug）。

**迁移要点：** 审计 X5 时加一问：**配置被钳制或回落到默认值时，调用方能知道吗？** 静默钳制与静默漂移是同一类问题的两种方向，都要判 `RISK`。

---

## 模式 35 · 反例入册（案例 4 的三个，其中一个其实是正例）

**服务的不变式：** M1、X1、X5、L2

### 反例 A · 两层默认值不一致，审计看不出来

**证据：** `QueryEngine.__init__` 的默认是 `max_turns: int | None = 8`；但 `src/openharness/engine/query.py` 的 `QueryContext` 默认是 `max_turns: int | None = 200`。走 `QueryEngine` 的调用方拿到 8 轮，直接构造 `QueryContext` 的调用方拿到 200 轮。

**可迁移的判据：** 这是 SKILL.md L2 里那条「那条路径根本没接到配置」的现场。同一个上限参数，两条构造路径给出 25 倍不同的默认值，而且**配置审计查不出来**——配置是对的，只是有另一条路没走它。审计 L2 时不要只问「上限有没有下传」，要问**「每一条构造路径的默认值是不是一致的」**。

### 反例 B · 累加型聚合器没有执行点

**证据：** `src/openharness/engine/cost_tracker.py` 全文 25 行：

```py
class CostTracker:
    def __init__(self) -> None:
        self._usage = UsageSnapshot()
    def add(self, usage: UsageSnapshot) -> None:
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + usage.input_tokens,
            output_tokens=self._usage.output_tokens + usage.output_tokens,
        )
```

只累加 input/output，**丢弃 cacheRead / cacheWrite / cost**，且**没有任何地方读它来做限用**。

**可迁移的判据：** 这是 M1 第三档的现场：「计数器存在却没人把它当限用」。两个独立缺陷叠在一起——(1) 累加器本身字段不全，回答不了「花了多少钱」（X1 的判据：聚合器丢字段，回答不了该回答的问题）；(2) 即使字段全，也没有执行点。**字段全 + 无执行点 = M1 的 GAP；字段不全 + 无执行点 = M1 的 GAP 外加 X1 的 GAP。** 审计时两条要分开报，因为修法不同（补字段 vs 接执行点）。

### 反例 C（其实是正例）· 硬拒层在用户规则之前求值，不可被配置覆盖

**证据：** `src/openharness/permissions/checker.py`——敏感路径名单（`.ssh/*`、`.aws/credentials`、`.docker/config.json`、`.kube/config` 等）在 `evaluate()` 的**最前面**求值，早于工具黑名单、白名单、路径规则、命令模式、权限模式：

```py
# Built-in sensitive path protection — always active, cannot be
# overridden by user settings or permission mode.
```

而且 `FULL_AUTO`（全放行）也**不覆盖这一层**——全放行分支在敏感路径检查之后。配套的一个细节：`_policy_match_paths` 同时匹配 `path` 和 `path + "/"`，让 `*/.ssh/*` 这类模式能命中目录根本身。

**为什么值得学：** 这与案例 2 反例 A（建议性护栏被当硬边界）正好相对。那里的问题是「唯一的边界是建议性的」，这里给出的是正向形态：**存在一条不可被配置覆盖的硬拒层，且它的求值顺序排在所有可配置项之前**。求值顺序是这里的实质——`FULL_AUTO` 分支放在敏感路径检查之前，这条硬拒层就形同虚设。判 M13 时问第四问：**有没有一层是配置改不动的？它的求值顺序在可配置项之前还是之后？**

**注意：** `_policy_match_paths` 那处斜杠兼容是**运行时检查型**的防线（案例 3 的 §0 判据），不是结构性的。判 M13 时按 §0 追加一问「覆盖了几条分支」——它只处理了目录根这一个歧义，没有处理大小写、Unicode 规范化、路径穿越（`..`）这些。

---

## 案例 5 · harness/harness（2026-09-08 上游 `main` 源码核验）

来源：[github.com/harness/harness](https://github.com/harness/harness)。Go 写的 CI/CD 与 artifact 平台，**没有 LLM，没有 agent loop**。它进这个库的原因只有一个：**它证明了这组不变式是调度类系统的通用属性，不是 LLM 特有的**。它的 scheduler、queue、canceler 在 M3、L1、L3、L6、L7、X5 上都有可对照的具体判据。

## 模式 36 · 可丢的唤醒信号需要有限轮询兜底

**服务的不变式：** L1、L7

**做法：** `app/pipeline/scheduler/queue.go`——唤醒用容量为 1 的 channel，发送是非阻塞的，**信号可以被丢弃**：

```go
q := &queue{
	ready:    make(chan struct{}, 1),
	interval: time.Minute,
}

func (q *queue) Schedule(_ context.Context, _ *types.Stage) error {
	select {
	case q.ready <- struct{}{}:
	default:  // 满就丢
	}
	return nil
}

func (q *queue) start() error {
	for {
		select {
		case <-q.ctx.Done():
			return q.ctx.Err()
		case <-q.ready:
			if err := q.signal(q.ctx); err != nil {
				// don't return, only log error
			}
		case <-time.After(q.interval):   // 1 分钟轮询兜底
			if err := q.signal(q.ctx); err != nil {
				log.Ctx(q.ctx).Err(err).Msg("failed to signal on interval")
			}
		}
	}
}
```

**为什么值得学：** 一条此前没被写出的判据。**基于信号的唤醒如果允许丢弃，就必须有有界轮询兜底，否则丢信号就是活性 bug**——不是正确性 bug，是「这个 stage 永远不会被调度，而没有任何错误」，比报错更难查。这里容量 1 + 非阻塞发送的设计是**正确**的：它允许合并多次唤醒成一次（省掉无谓的 `signal` 调用），正确性由 1 分钟轮询兜住。`signal` 的返回错误也被吞掉并只记日志——**循环不因瞬时故障而死**，这也是活性设计。

**迁移要点：** 审计任何 `select` / `channel` / event-driven 唤醒时问两句：(1) 唤醒能不能丢？(2) 如果能丢，兜底轮询的上限是多少？**答「不能丢」但发送是非阻塞的，就是 L1 的 GAP**——声明与实现不一致。

---

## 模式 37 · 终态编码「这个单元有没有真的启动过」

**服务的不变式：** L1、L7

**做法：** `app/pipeline/canceler/canceler.go` 与 `app/pipeline/manager/teardown.go`——取消与收尾都用 `Started != 0` 区分「已被杀掉」和「被跳过」：

```go
if stage.Started != 0 {
	stage.Status = enum.CIStatusKilled
} else {
	stage.Status = enum.CIStatusSkipped
	stage.Started = now
}
```

被杀的步骤还写死退出码 130（`step.ExitCode = 130`）。取消本身是幂等的，入口先做状态守卫：

```go
// do not cancel the build if the build status is complete.
if execution.Status != enum.CIStatusPending &&
	execution.Status != enum.CIStatusRunning {
	return nil
}
```

终态聚合（`teardown.go`）不是简单取最后一个状态，而是按优先级折叠：默认 `Success`，遇到 `Killed` 则 killed，遇到 `Failure` 则 failure，遇到 `Error` 则 error。

**为什么值得学：** 案例 1 模式 4 的三条终态讲的是**循环**的终态；这里讲的是**单元**的终态，且补了一个之前没有的判据：**终态要能回答「它到底跑过没有」**。`Skipped` 和 `Killed` 在「不是成功」这一点上等价，但在「有没有消耗资源」「要不要计入失败率」「下游该不该跑」上完全不同。`ExitCode = 130` 是把这个区分编码进下游契约的方式。

取消的幂等性判据也值得记：**幂等不等于无操作**。这里取消一个已完成的 execution 会返回 `nil`（成功）而不是错误——重复取消是合法的调用，不该被报成失败。

**迁移要点：** 审计 L1 时问：终态枚举能不能回答「这个单元消耗了资源吗」？答不上时，把所有「非成功」状态折叠成一个值，就是缺陷形态。

---

## 模式 38 · 并发与限流守卫不追溯已在跑的工作

**服务的不变式：** L3、X5

**做法：** `app/pipeline/scheduler/queue.go`——两个守卫函数都有显式的排除集合：

```go
func withinLimits(stage *types.Stage, siblings []*types.Stage) bool {
	if stage.Limit == 0 { return true }          // 默认值恰好是无上限
	for _, sibling := range siblings {
		if sibling.RepoID != stage.RepoID { continue }   // 排除：不同仓库
		if sibling.ID == stage.ID { continue }           // 排除：自己
		if sibling.Name != stage.Name { continue }       // 排除：不同 stage
		...
	}
}

func shouldThrottle(stage *types.Stage, siblings []*types.Stage, limit int) bool {
	if limit == 0 { return false }
	// if the repository is running it is too late
	// to skip and we can exit
	if stage.Status == enum.CIStatusRunning { return false }
}
```

`matchResource` 则在比较点统一补默认值：`kinda == ""` → `"pipeline"`，`typea == ""` → `"docker"`，四个参数用同一套默认，避免某个比较器漏补一个默认值。

**为什么值得学：** 三条判据合在一起：**守卫要排除自身**（否则自己把自己算进计数，第一个 stage 就会把自己锁在外面）、**限流不追溯**（已经在跑的不因为超配而被打断——「已经晚了」是显式设计）、**比较器共享默认值**（X5 判据：所有比较器用同一套归一化，缺失值才无法以歧义形式逃出）。

第三条尤其值得对照：**`Limit == 0` 表示无上限**——默认值恰好是危险值。这是案例 2 反例 B「省略即重置」的第二个独立来源，且这次是数值型而不是配置省略。

**迁移要点：** 审计 L3 时问：**守卫的计数会不会包含被守卫对象自己？** 这是并发计数类缺陷最常见的一个，且只在一个元素时不触发（第一个元素把自己算进去也才 1 ≤ limit），在第二个元素时才开始表现，测试很难覆盖。

---

## 模式 39 · 收尾不能绑定触发它的取消令牌，清理失败要按层分级

**服务的不变式：** L5、L6、L7

**做法一：用 `noContext`，不用触发收尾的那个 ctx**（`app/pipeline/manager/teardown.go` 全文）——`do()` 接收 `ctx`，但所有 store 读写都用 `noContext`；只有下游取消、下游调度、事件上报三处用传入的 `ctx`：

```go
stages, err := t.Stages.ListWithSteps(noContext, execution.ID)
err = t.cancelDownstream(ctx, stages)
err = t.scheduleDownstream(ctx, stages)
```

**做法二：乐观锁冲突时 resync，且 resync 故意不更新 Version 字段**：

```go
// resync updates the stage from the database. Note that it does
// not update the Version field. This is by design. It prevents
// the current go routine from updating a stage that has been
// updated by another go routine.
```

**做法三：清理错误按层分级**——step / stage 写入失败一律硬返回；log stream 删除失败降级为 warn；但 `checks.Write`（GitHub status check）失败**只记日志并继续**，注释写明「try to write to the checks store - if not, log an error and continue」。

**为什么值得学：** 做法一是 L5 的**新判据**：**收尾/清理路径不能继承触发它的那个取消令牌**。一个被取消的 execution 触发自己的 teardown，teardown 用同一个 ctx，取消传播会让 teardown 中途退出——留下「父已标记 killed、子仍标记 running」的中间状态。用 `noContext`（或等价的 uncancelable context）让收尾有独立的生命周期。

做法二是模式 29 的对照：案例 3 用状态机字段表达「下一步是什么」，这里用**故意不更新版本号**表达「我不再参与这个对象的并发竞争」。同样是「让错误状态不可表达」，但手段是**放弃可变性**而不是拆分类型。

做法三是**有意的**分级，且写进了注释。对照反例：`canceler.go` 里 execution 级更新失败是硬错误（`return fmt.Errorf(...)`），而 stage / step 级更新失败是 `log.Debug()` 并吞掉——**父可以持久化为 killed，子仍显示 running**，产生不一致的状态树。这是 pi 的模式 31（清理只删当前状态标 pending 的部分、错误不吞）的镜像反例。

**迁移要点：** 审计 L5/L6 时问：**收尾路径的 context 来自哪里？** 来自触发它的取消/超时操作时，就是 L5 的 GAP——收尾本身成了可被取消的对象。审计错误分级时问：**哪些清理是「必须成功否则状态不一致」的？** 这类不能吞；「删临时日志」这类可以吞但要留痕。分级必须有注释说明理由，否则下一个维护者会统一掉。

---

## 模式 40 · 反例入册（案例 5 的一个）

**服务的不变式：** L6、L7

**证据：** `app/pipeline/canceler/canceler.go`——同一个函数里的失败分级不对称：

```go
err := s.executionStore.Update(ctx, execution)
if err != nil {
	return fmt.Errorf("could not update execution status to canceled: %w", err)   // 硬错误
}
...
err := s.stageStore.Update(ctx, stage)
if err != nil {
	log.Debug().Err(err).Msg("canceler: cannot update stage status")              // 吞掉
}
...
err := s.stepStore.Update(ctx, step)
if err != nil {
	log.Debug().Err(err).Msg("canceler: cannot update step status")               // 吞掉
}
```

**可迁移的判据：** 这是「部分失败产生不一致状态树」的现场形态，且**是分级错误的方向**：父级失败是硬错误、子级失败是 Debug 级吞掉。结果是一个持久的、可观测的不一致——UI 显示整个 build 被 killed，但某个 stage 仍显示 running。对照 harness/harness 自己 `teardown.go` 的做法（`cancelDownstream` / `scheduleDownstream` 用 `multierror.Append` 收集所有错误再返回），同一仓库里两种写法并存。

更值得记的是**这条不是笔误**：canceler 的语义是「取消是尽力而为，父先落盘」。这个选择在崩溃恢复场景下有道理（先保证父状态可查）。但正确的写法是把「尽力而为」和「静默不一致」分开——**尽力而为可以吞错误，但必须把「有子单元没同步」这件事变成可观测的状态**，而不是靠 `log.Debug()`。判 L6 时这就是 RISK 的判据：部分失败的后果没有被编码进数据，只被编码进了日志。

**迁移要点：** 审计「父-子状态树」时问：**子级更新失败会怎样？** 答「记日志」的，要再问一句「有谁能观测到这个不一致」——如果没有，就是反例形态。

---

## 案例 6 · hermes-agent（NousResearch/hermes-agent，2026-09-08 上游 `main` 源码核验）

**核验范围：** 取回 18 个文件（`agent/conversation_loop.py`、`agent/turn_recovery.py`、`agent/moa_loop.py`、`agent/turn_overflow.py`、`agent/turn_truncation.py`、`agent/turn_tool_validation.py`、`agent/turn_stop_gates.py`、`agent/tool_guardrails.py`、`agent/empty_response_guard.py`、`hermes_cli/checkpoints.py`、`agent/turn_liveness.py`、`agent/turn_retry_state.py`、`agent/repetition_guard.py`、`agent/model_cost_guard.py`、`agent/periodic_scheduler.py`、`agent/turn_preflight_gate.py`、`agent/iteration_budget.py`、`tools/retry_utils.py`）。3 个大文件（`conversation_loop.py` 82KB、`turn_recovery.py` 76KB、`moa_loop.py` 74KB）只做头部通读 + 定向检索，未逐行读完——**引行号前先确认版本**。

**与案例 3–5 的区别：** 案例 3、4 是 LLM agent 框架/引擎，案例 5 是非 LLM 的调度系统。案例 6 是**面向终端用户的完整 agent 产品**（CLI/TUI/desktop/API server 多平台），它带的是「一个产品要同时满足交互体验和无人值守安全」这一层约束——这条约束在案例 3–5 里都没出现，所以本案例多数锚点是「同一套不变式在不同运行模式下的分层」，而不是「这条不变式的新做法」。

**证据等级：** 全部 `[源码确认]`，绑定 2026-09-08 快照。全部是项目自身的实现，不涉及第三方框架 API 名。

---

### 模式 41 · 重试预算要乘上单次尝试成本

**服务的不变式：** M1

**证据：** `agent/empty_response_guard.py`

```python
DEFAULT_EMPTY_RETRY_BUDGET = 3
REDUCED_EMPTY_RETRY_BUDGET = 1
DEFAULT_COST_THRESHOLD_USD = Decimal("0.25")

def empty_retry_budget(agent: Any, response: Any) -> int:
    """Empty-retry budget for the current streak (3, or 1 when a single attempt is
    estimated to cost more than the configured threshold)."""
    if not guard_enabled(agent):
        return DEFAULT_EMPTY_RETRY_BUDGET
    cost = _estimate_attempt_cost(agent, response)
    if cost is not None and cost >= _cost_threshold_usd(agent):
        return REDUCED_EMPTY_RETRY_BUDGET
    return DEFAULT_EMPTY_RETRY_BUDGET
```

预算是「次数」，单次尝试成本是「价格」，两者相乘才是花费。固定 3 次重试在单次成本 $0.001 时合理，在单次成本 $1 时是烧 $3 换同样一个结果。此前只有「要有成本上限」（M1）和「分类可替换」（L8），没有把「重试预算」和「单次成本」显式连起来的写法。

**可迁移的判据：** 预算收缩要落到具体数字——阈值、N、M 三个数都要能引出来（`$0.25`、`3`、`1`）。审计不要只问「有没有成本上限」，要问「成本维度影响了哪些预算」；影响到了，判 `OK` 的前提是收缩方向与幅度都能反查。

**同时注意失败方向：** `cost is not None and cost >= threshold` —— 成本未知（`None`）时预算不动，走默认 3 次。这是**保守方向的 fail-open**：宁可能在未知成本下多试两次，也不因定价数据缺失把重试掐掉。方向本身就是判据，见 M13 第四条。

**迁移要点：** 判 M1 时问：预算收缩条件里的「未知」走哪一档？「未知就走默认预算」合法，但要写出来；「未知就走最严」会让定价服务故障变成 agent 功能退化。

---

### 模式 42 · 确定性检测要拒绝混合证据，签名要覆盖 provider 维度

**服务的不变式：** M11、M7

**证据：** `agent/empty_response_guard.py`

```python
@dataclass(frozen=True)
class EmptyAttempt:
    model: str
    provider: str
    finish_reason: str
    usage_present: bool
    zero_output: bool
    observed_generation: bool

    @property
    def signature(self) -> tuple:
        return (self.model, self.provider, self.finish_reason)

def deterministic_empty(agent: Any) -> bool:
    """...Requires >= 2 consecutive attempts with an identical
    (model, provider, finish_reason) signature. Usage-backed attempts must all
    prove zero output. Usage-absent attempts must all have no observed content
    or reasoning. Mixed evidence fails open so ambiguous transients keep their
    retries."""
    ...
    same_signature = all(a.signature == first.signature for a in attempts)
    usage_proves_empty = all(a.usage_present and a.zero_output for a in attempts)
    response_proves_empty = all(
        not a.usage_present and not a.observed_generation for a in attempts
    )
    return same_signature and (usage_proves_empty or response_proves_empty)
```

两件事绑在一起：

1. **签名包含 provider**——同一个 `model` 名经不同 provider（直连 / 代理 / 网关）时，`finish_reason` 语义可能不同；签名不含 provider 会把「同一模型的两种失败」误判成「同一失败」。
2. **混合证据 fail open**——两次空响应一次有 usage 一次没有、或一次有 reasoning token 一次没有，`deterministic_empty` 返回 `False`，保留重试。方向是刻意的：把瞬态误判成确定性会让「本来能重试成功」的回合直接跳去 fallback 链，代价比多试一次大得多。

**可迁移的判据：** 做「确定性」判定（确定性失败、幂等、可重试）时，签名要覆盖**能改变语义的所有维度**，不只是「模型名」；判定条件是 `all` 不是 `any`，混合证据意味着你其实不知道发生了什么，此时保守选择是保留原行为。

**迁移要点：** 审计 M11 时问：签名里少了哪个维度？「模型名 + finish_reason」在直连场景够用，多 provider 场景会错分；`any` 判定的形态要把每次判定记出来，让「证据不一致」可观测，而不是被 `any` 静默吞掉。

---

### 模式 43 · 状态清零要由消费方负责，而不是由记录方

**服务的不变式：** M9、M10

**证据：** `agent/empty_response_guard.py`

```python
# Agent-object attribute names. State is scoped to one consecutive empty streak: cleared
# whenever ``_empty_content_retries == 0`` at record time, so every existing counter-reset
# site (turn start, compaction, tool success, fallback activation) is honoured.
_ATTEMPTS_ATTR = "_empty_attempt_history"

def record_empty_attempt(...):
    """Record one empty completion in the current streak.

    Call BEFORE ``_empty_content_retries`` is incremented: a counter of 0 marks a new
    streak and clears prior history."""
    attempts = _attempts(agent)
    if getattr(agent, "_empty_content_retries", 0) == 0:
        attempts.clear()
        setattr(agent, _STREAK_COST_ATTR, Decimal("0"))
```

「streak」的生命周期不该由记录方自己判断——turn start、compaction、tool success、fallback activation 四个位置都会重置计数器，记录方不需要知道它们的顺序，只需在记录时读一次「计数器是不是 0」。这把「谁负责清理」这个通常要维护成清单的约束，换成了**单一判据点**。

注释里还有更细的时序约束：`Call BEFORE _empty_content_retries is incremented`——先记录、再递增，才能让「0」区分「新 streak」和「streak 内的第二次」。反过来递增再记录，新 streak 的第一次记录会被判成「计数器非 0」，历史不清，跨 streak 的失败会被当成同一个 streak 的连续失败，直接跳过重试。

**可迁移的判据：** 状态作用域的清理责任要么集中在一处（有清单可维护），要么换成消费方读一次即可判的单一判据。前者要维护「新增了 reset 点、清清单了吗」；后者把维护义务从「记清单」变成「改判据」，判据形态更稳。

**迁移要点：** 审计 M9 时问：状态的清零由谁负责？一处一处清的，逐个位置确认，漏一处不清就是 `GAP`；消费方按单一判据清的，判据本身可判。

---

### 模式 44 · 重试上限与退避表必须一起算，算式要写出循环退出顺序

**服务的不变式：** L8

**证据：** `tools/retry_utils.py`

```python
_ZAI_CODING_OVERLOAD_LONG_BACKOFF = (30.0, 60.0, 90.0, 120.0)
_ZAI_CODING_OVERLOAD_SHORT_ATTEMPTS = 3
# The short count is shared by ``adaptive_rate_limit_backoff`` and
# ``zai_coding_overload_retry_ceiling`` so the two cannot silently desync.

def zai_coding_overload_retry_ceiling(short_attempts: int = _ZAI_CODING_OVERLOAD_SHORT_ATTEMPTS) -> int:
    """Retry-loop ceiling for the full Z.AI overload schedule: one past the last long entry,
    because the loop gives up when ``retry_count >= ceiling`` BEFORE computing the attempt's
    backoff (the default ``api_max_retries`` of 3 equals ``short_attempts``)."""
    return short_attempts + len(_ZAI_CODING_OVERLOAD_LONG_BACKOFF) + 1
```

三个细节：

- **+1 不是安全余量，是循环退出顺序的推论**：循环在计算本次退避**之前**判断放弃，上限等于表长时最后一档退避永远不执行。审计重试表要看上限能不能取到表里每一档。
- **共用常量**：`_ZAI_CODING_OVERLOAD_SHORT_ATTEMPTS` 同时被退避函数和上限函数消费，注释写「so the two cannot silently desync」——同一个数字写在两处，改一处忘改另一处不报错，只会让退避表和上限永久不一致。
- **分类器是窄的**：`is_zai_coding_overload_error` 要求 status 429 + 精确 base_url 片段 + 精确 model 名 + 错误文本含 `1305` 或 `temporarily overloaded`，四条件同时满足才走加长退避。普通 429 走常规路径、快速失败。分类器越窄误匹配代价越小；宽分类器加宽退避等于把「限流」当成「暂时过载」，让所有 429 都等 30–120s。

**可迁移的判据：** 退避表和上限要能算出来；共用常量要单一来源；分类器要窄到能引用出全部判定条件。

**迁移要点：** 判 L8 时不要只看「有没有上限」，要拿一个已完成的真实重试序列，数循环里用了哪几档退避；表尾档位从未出现，就是上限少算了 1（或循环退出顺序和注释不一致）。

---

### 模式 45 · jitter 的随机源要额外去相关，防粗时钟把并发重试重新同步

**服务的不变式：** L8

**证据：** `tools/retry_utils.py`

```python
# Monotonic counter for jitter-seed uniqueness within a process; locked
# because concurrent gateway sessions retry simultaneously.
_jitter_counter = 0
_jitter_lock = threading.Lock()

def jittered_backoff(attempt, *, base_delay=5.0, max_delay=120.0, jitter_ratio=0.5):
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    delay = max_delay if (exponent >= 63 or base_delay <= 0) else min(base_delay * (2 ** exponent), max_delay)

    # Seed from time + counter so coarse clocks still decorrelate.
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    return delay + random.Random(seed).uniform(0, jitter_ratio * delay)
```

两处反直觉的防御：

- **指数防溢出**：`exponent >= 63 or base_delay <= 0` 时直接返回 `max_delay`，不让 `2 ** exponent` 算到天文数字。防的是「jitter 之后再乘系数」把封顶值放大的路径——如果先算 `2 ** 63` 再 `min(..., max_delay)`，中间值已超界，某些运行时行为不同。
- **种子不只是时间戳**：并发会话在同一纳秒内会拿到相同种子，纯时间戳种子会把 thundering herd 完整重新造出来。加锁单调计数器是**进程内**的去相关手段——防「同进程内并发会话」，不防「跨进程」，跨进程仍靠时间戳粒度。

注释「so coarse clocks still decorrelate」说的是：在时钟粒度只有毫秒（甚至秒）的运行时上，时间戳种子基本无效，必须额外注入单调量。

**可迁移的判据：** jitter 的随机源在两个前提下要额外处理：(1) 并发会话可能同时重试；(2) 运行时时钟粒度不足以区分它们。任一成立，纯时间戳种子退化。

**迁移要点：** 审计 L8 的 jitter 时问：**这个进程里可能同时有几个会话在重试？** 答「只有 1 个」可直接用时间戳；答「多个」要看种子构造，纯时间戳就是 `RISK`。

---

### 模式 46 · 配置解析器要主动拒绝会翻转控制流方向的值

**服务的不变式：** X5、M1

**证据：** `agent/turn_liveness.py`

```python
def _resolve_finite_seconds(raw: Any, *, default: float, key: str) -> float:
    """Coerce one duration knob, rejecting typos, NaN and Inf (never raises).

    NaN would silently disable the timeout via the ``> 0`` comparison and
    Inf would freeze the poll loop in ``Event.wait``.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value):
        _warn_invalid_value(key, raw, default)
        return default
    return value
```

NaN 与 Inf 都在「合法浮点数」范围内，纯范围校验抓不到，但破坏方式是**改变比较与等待的语义**——NaN 让所有比较返回 `False`（守卫不触发），Inf 让 `Event.wait(Inf)` 变永久等待。这两个失效模式不是「数值变大」，是「守卫方向翻转」。

同类形态还有一处在 `agent/empty_response_guard.py`：

```python
enabled = section.get("enabled", DEFAULT_GUARD_ENABLED)
if isinstance(enabled, str):  # YAML quoting can turn true/false into strings.
    enabled = enabled.strip().lower() not in ("0", "false", "no", "off")
elif not isinstance(enabled, bool):
    enabled = DEFAULT_GUARD_ENABLED
```

YAML 引号会把 `false` 变成字符串 `"false"`，此时 `bool("false")` 是 `True`——不加这段处理，**「关掉的护栏」会被读成「开着的护栏」**。这档比 NaN 更常见，不需要用户主动写 NaN，只要用了引号。

**可迁移的判据：** 数字配置的失效清单分两半——**越界那半边**（越大、越小、负数、零）和**翻转那半边**（NaN、Inf、`null`、空串、类型错位）。翻转类值不报错、不产生日志，只让守卫安静地不再生效，形态上和「配错了 key 静默回落默认值」同源。审计把两半分开问，两半都要有清单。

**迁移要点：** 判 X5 时问：**哪些配置值会让守卫从「在防」变成「没在防」，而不是让守卫更严？** 答不上来的项目只覆盖一半。解析器对引号敏感的类型（bool、enum）必须自己处理字符串回落。

---

### 模式 47 · 观察型报告与已提交报告要分成两次发布，措辞要匹配

**服务的不变式：** L6、L5

**证据：** `agent/turn_liveness.py`

```python
def _tick(self):
    ...
    if snapshot.idle_seconds < self._timeout_s:
        return None
    # Pre-commit surface is OBSERVATIONAL only: it reports the stall and that a recovery attempt is
    # beginning. It must not claim the abort or the lease withdrawal has committed — the next operation
    # can still veto the outcome. The definitive aborted/lease-stopped settlement is published by
    # _surface_committed_abort only after _commit_abort succeeds and the turn is deactivated (#95663
    # review).
    self._surface_stall(snapshot)
    message = f"Turn made no progress for {int(snapshot.idle_seconds)}s; aborting to release the session."
    if not self._commit_abort(snapshot, message):
        return None
    # Stop renewing the lease so a wedge the interrupt cannot unwind expires via TTL.
    self._deactivate_turn()
    self._surface_committed_abort(snapshot)
    return False
```

```python
def _surface_committed_abort(self, snapshot: ActivitySnapshot) -> None:
    """Publish the definitive settlement once the abort has authority.

    Runs only once ``_commit_abort`` succeeded (the interrupt was published) and the turn lease was
    deactivated: the turn IS force-aborted and lease renewal IS stopped, so stating that is now true.
    Separated from the pre-commit surface so a declined abort never reports a committed outcome (#95663
    review).
    """
```

两段发布的措辞完全不同：pre-commit 只说「检测到停滞，正在尝试恢复」，committed 才说「已被中止，租约已停止更新」。中间存在**被否决**这条路径（`_commit_abort` 返回 `False`，说明有进展、watchdog 判定被推翻），此时如果 pre-commit 已报告「已中止」，用户看到的状态就比实际状态超前。

**可迁移的判据：** 任何「报告 → 操作 → 可能有另一个操作否决」的形态，报告要分两次发。判据问：**报告里承诺的结论，是不是在发这条报告时就已经不可撤销了？** 答「不是」就要拆成两次。

**迁移要点：** 审计 L6 时把「终态要能区分」扩展到用户可见的报告面——L6 原有判据针对数据字段（下游读到什么），这里是同一原则在日志/通知/UI 上。报告超前实际状态，是 L6 的另一种形态：不是数据不一致，是**用户以为数据一致**。

---

### 模式 48 · 删除授权要绑定到确认时看到的那一批身份

**服务的不变式：** L5、M13

**证据：** `hermes_cli/checkpoints.py`（`hermes checkpoints prune`）

```python
# Bind deletion to exactly what was displayed/confirmed: a project that goes orphan
# only *after* the preview (workdir vanishes while waiting on input()) must not be
# swept up. Set unconditionally for every non-force run — an EMPTY preview binds an
# EMPTY allowlist, so it can never authorize orphans found by the later rescan.
orphan_allowlist = {p["hash"] for p in orphans}
orphan_allowlist.update(p["path"] for p in pre_v2_orphans)
```

确认提示里把「不可达」的两义性直接写出来：

```python
print("A workdir can be unreachable because the project was deleted,")
print("or because an external volume / network share / VPN is down.")
print("Pass --keep-orphans to prune stale entries only.")
```

三个细节：

- **allowlist 无条件设置**——注释明确写「an EMPTY preview binds an EMPTY allowlist」。如果只在「有 orphan 时」设置，「预览时没有、确认时有」这条路径就绕过绑定；这条路径正是 TOCTOU 的入口。
- **`--keep-orphans` 走窄路径**——只删 stale，不碰 orphan。这把「网络故障 = 批量删除」这条路径关掉了。
- **两义性写在提示里**——用户看到 `workdir` 不可达时，代码直接告诉他可能是 VPN 断了，而不是让他在 `prune` 这个词上猜。

**可迁移的判据：** 破坏性操作前的确认预览，其授权范围要绑定到预览时的那批身份；预览为空时授权也必须为空。任何「不可达」类信号在驱动删除时都要有窄化开关。

**迁移要点：** 审计 L5/L13 时问：**预览之后、删除之前有时间差吗？** 有，就要问「这段时间里新出现的对象会被授权吗」，答「会」就是反例形态。

---

### 模式 49 · 交接字段清单要从接收方自己的声明派生

**服务的不变式：** M9

**证据：** `agent/conversation_loop.py`

```python
# Post-loop finalization lives in agent/turn_finalizer.finalize_turn.
result = finalize_turn(agent, **{
    name: getattr(s, name)
    for name in inspect.signature(finalize_turn).parameters if name != "agent"
})
```

字段清单不是手写枚举，是从 `finalize_turn` 自己的签名里读出来的。`finalize_turn` 加了一个参数，调用点自动带上；不需要改两处。

对照反面：手写枚举的清单漏一个字段不报错，接收方拿到 `None`（或默认值）后照常跑完，「状态没传过去」被当成「这个字段本来就空」。这档比「漏传报错」更坏——报错至少能被看到，静默漏传会让缺失一路传到最终输出。

**可迁移的判据：** 跨模块交接的字段清单，**派生式**优于**枚举式**：从接收方的声明（签名、schema、接口）自动派生，让「接收方变了」这个动作成为唯一改动点。枚举式清单要求每次改动两处，两处不一致不报错。

**迁移要点：** 审计 M9 时问：**两处之间的字段清单是怎么保持同步的？** 答「靠人记得改」就是枚举式，`RISK`；答「靠签名/schema 自动派生」是 `OK`。

---

### 模式 50 · 部分失败的编码方式要可配置，且判据看默认方向

**服务的不变式：** L6、L9

**证据：** `agent/moa_loop.py`

```python
# 配置项 degraded_reference_policy，默认 "loud"

def _degraded_notice(failed_labels, policy, reference_outputs):
    """返回 "" 当且仅当 not failed_labels or policy.strip().lower() == "silent"，
    否则返回 "[Reference models unavailable: labels]" 追加进聚合者可见上下文。"""
```

MoA 是 fan-out：多个 reference model 各自给出参考输出，聚合者（也是模型）看到编号的参考块并自己决定。关键不是「聚合者做不做判断」（见模式 51），而是**失败信息进不进聚合者能看到的输入**：

- `loud`（默认）：失败的 reference 从输出里过滤掉，同时把 `[Reference models unavailable: labels]` 追加进上下文。聚合者知道自己少了几份样本。
- `silent`：完全静默。聚合者在不知道样本缺失的情况下做出判断。

两种实现都存在，**默认是 loud**。这是 L6「部分失败必须编码进数据」的**可配置形态**——编码方式不再是强制的，但默认方向仍然是判据。默认 `silent` 的部分失败等于把「聚合器在降级条件下工作」伪装成「聚合器正常工作」，和 L10 的「证据缺失时判过」同构。

同文件 `_sum_reference_accounting`：按每个 advisor 自己的费率汇总成本，失败槽位不参与计价——**失败槽位只影响计数，不影响成本口径**。混进计价的分母会让「这次更贵」和「这次少一个人干活」混成同一个信号。

**可迁移的判据：** 部分失败的编码可以做成策略开关，但要判**默认方向**。默认静默的策略 = 默认把降级伪装成正常；判 X5 的「配置漂移」时记成「默认值是不可观测的降级」，不是「配置项存在即可」。

**迁移要点：** 判 L6 时问：**部分失败的策略默认值是什么？** 默认可观测（loud / 编码进数据）= `OK`；默认静默 = `RISK`，要写出来「这条策略有静默选项，用户可能打开」。判 L9 时这一条只能算半个锚点——它证明「降级要可见」（模式 50）和「裁判是谁要说清楚」（模式 51），不证明「冲突能被检出」。

---

### 模式 51 · Fan-out 的聚合者是模型本身，「谁是裁判」是判据不是实现细节

**服务的不变式：** L4、L9

**证据：** `agent/moa_loop.py`

```python
# 模块 docstring 摘：
# The slash command marks one user turn as MoA-enabled; the normal agent loop still
# owns tool calling and turn termination, while this module gathers reference-model
# context before each model iteration.
```

MoA 的架构选择：**reference model 只作为上下文提供，不做判断**。聚合者（主模型的下一轮调用）看到编号的参考块，自己决定采纳哪些。这里没有第二个 LLM 去比对「两个 reference 是否矛盾」。

**可迁移的判据：** 判 L9「跨 agent 一致性」时先问**「谁是裁判」**。三种形态：

1. 唯一事实来源字段（案例 3、4 的形态）——冲突在构造期不可表达。
2. 聚合者是模型本身，看编号参考块自己判——**没有冲突检测**，只有「参考意见」的加权。
3. 另一个 LLM 比对两份结论——退化成 M12 的「让作者批改自己的作业」形态。

形态 2 在 L9 上是 `RISK` 不是 `OK`：它确实让多份意见可见，但没有一个分支会因为矛盾而停下来。审计要判的不是「有没有多 agent」，是「多 agent 之间有没有一个能因为矛盾而停下来的机制」。

**为什么仍然值得引：** 这是 L9 的**第一个非空锚点**，但是**半锚点**——证明「降级要可见」（模式 50）和「裁判是谁要说清楚」（模式 51），不证明「冲突能被检出」。挂载索引里 L9 因此仍记为空（半锚点按原项判），但 L4 的「分支条件必须可机器判定」多了一个正面案例：MoA 的终止条件仍是主 loop 的终止条件（`owns tool calling and turn termination`），reference model 的输出不进入终止判据——正是 L4 要的「不依赖模型自判」。

**迁移要点：** 判 L4 时问：**哪些条件是可机器判定的？** 判 L9 时问：**裁判是谁，裁判会不会因为矛盾停下来？** 两个问题的答案组合出四种形态，四种都要在报告里写清。

---

### 模式 52 · 反例入册：诊断字段存在但没人填

**服务的不变式：** L1、X1

**证据：** `agent/conversation_loop.py`

```python
_turn_exit_reason: str = "unknown"  # diagnostic: why the loop ended
```

取回的 18 个文件里，`_turn_exit_reason` 只赋值 1 处（`agent/turn_preflight_gate.py` 的 `v._turn_exit_reason = "ollama_runtime_context_too_small"`）；同名的结果契约字段 `turn_exit_reason` 只赋值 3 处，全部是上下文压缩超时类终态。可识别的终止路径约 15 条。

**这不是「字段缺失」**——字段存在，注释写着 `# diagnostic: why the loop ended`，默认值 `"unknown"`。问题是**默认值等于没有**：13 条终止路径在观测面上看起来完全一样。

**可迁移的判据：** 判 L1「终态要能区分」时不要只问「有没有字段」，也不要只问「有没有赋值」，要问**比例**：终止路径总数 ÷ 被赋值的路径数。比例低于一半时，「终态可区分」在观测上等价于「全是一条路」，L1 第一条判据不成立，尽管代码里每条路径都写了 `return`。

对照案例 5：harness/harness 的 teardown 把 sibling 状态折成 execution 状态，每个终态都有名字。这里反例方向完全相反——**每条路径都有 return，但没有名字**。

**迁移要点：** 审计 L1/L6 时把判据写成可数的：**先数终止路径，再数被命名的路径**。两个数都是 grep 出来的，不需要读业务逻辑。比例低于一半 → `RISK`；低于三分之一 → `GAP`（观测面上根本分不清）。

---

### 模式 53 · 反例入册：预算豁免通道在本模块外，从 loop 侧不可审计

**服务的不变式：** L1、M1

**证据：** `agent/conversation_loop.py:1484`

```python
while (s.api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) or agent._budget_grace_call:
```

`agent._budget_grace_call` 是循环的豁免项——只要它为真，即使预算耗尽循环也继续。**它在整个 loop 模块里从未被写入**（取回的 18 个文件里只有这一处引用）。

**可迁移的判据：** 判 L1/M1 时问：**循环条件里引用了本模块不拥有的状态吗？** 有，就追问「这个状态谁能写、什么时候写、写了多久」。答不上来就是**不可审计的豁免通道**。

这档比「没有豁免」更严重：没有豁免是明面上的缺口（M1 直接判 `GAP`），不可审计的豁免是**看不见的缺口**——看起来像设计好的逃逸阀（grace call），但授权点不在 loop 自己的源码里，审计从 loop 侧无法判断。

**为什么仍然值得引：** 证明「条件里出现了守卫的例外」≠「守卫设计得完整」。反向检查方法：把循环条件里出现的每个布尔量列出来，逐个确认写入点在哪个模块、写入点是否可枚举。

**迁移要点：** 审计 M1/L1 时加一条：**列出所有豁免变量，逐个确认写入点**。写入点不可枚举（跨模块、跨进程、外部注入）的豁免通道，一律 `RISK` 起步。

---

### 模式 54 · 反例入册：预算是显式拒绝聚合，不是漏配

**服务的不变式：** L2

**证据：** `agent/iteration_budget.py`

```python
"""Per-agent iteration budget — thread-safe consume/refund counter.

Each ``AIAgent`` (parent or subagent) holds its own :class:`IterationBudget`: the parent's
cap is ``max_iterations`` (default 500), each subagent's ``delegation.max_iterations``
(default 50), so total iterations across parent + subagents can exceed the parent's cap.
"""
```

**这是设计意图，写在 docstring 里**——「parent + subagents 总迭代数可以超过父 agent 上限」是明说的。不是漏配聚合预算。

**可迁移的判据：** 判 L2 时把「漏配」和「显式拒绝」分开：

- **漏配**：代码里没写，配置里也没写。判 `GAP`，修法是补聚合预算。
- **显式拒绝**：代码或文档明确写出「不做聚合」并给出理由。判 `RISK`，要能引用到声明的那句话，修法是补一个跨层总闸或补成本维度兜底（见模式 41）。

两档修复成本不同：漏配要补代码，显式拒绝要补总闸或换维度兜底。**审计建议必须落到具体那一条**，不能混着给。

同类形态是案例 2 的「显式外包」（`while (true)` + 自文档「No built-in turn budget」）——案例 2 是「预算交给外部策略」，案例 6 是「每层局部上限，总和没有上界」。两者都是**显式外包**，方向不同但都是有意设计。

**迁移要点：** 判 L2 时问：**预算的形态是漏配还是显式拒绝？** 判据要有原文引用。判 X5 时同一条：显式写出的「不设上界」和漏配的「没有这一项」在配置审计上长得一样，只有读注释能区分——这一档的差异是**文档差异**，不是代码差异。

---

### 案例 6 小结

**新判据 14 条**（模式 41–54），全部落在既有 29 项不变式里，**没有新增不变式 ID**——这是这个案例的收获，也是它的边界：不变式表的骨架是 14+10+5，六个案例填进去 54 条模式，骨架没变。

**最扎实的一面：** 成本维度和重试预算的交叉（模式 41）、jitter 的并发去相关（模式 45）、配置翻转值的拒绝（模式 46）、报告与已提交的分离（模式 47）、删除授权的 TOCTOU 绑定（模式 48）、字段清单的派生式（模式 49）。这些在案例 1–5 里都没有对应写法。

**反例三条**（模式 52、53、54）——诊断字段没人填、豁免通道在本模块外、预算显式拒绝聚合。三条都是**「设计选择看起来像缺口」**的形态：不是缺陷，但审计不能跳过，因为从外部看它们和缺陷长得一样，唯一区分依据是文档/注释里写没写出来。

**未填的空行：** M12、M14 仍然空；L9 有半锚点（模式 50 + 51）但按原项判仍是空；X4 从空变成有锚点（案例 6 的 `_ATTENDED_PLATFORMS` 字面量集合 + `LoopCapConfig` 的开关分层，见挂载索引）。

---

## 挂载索引：不变式 → 模式

| 不变式 | 案例 1（模式 1–17） | 案例 2（模式 18–24） | 案例 3 · pi（25–31） | 案例 4 · OpenHarness（32–35） | 案例 5 · harness（36–40） | 案例 6 · hermes-agent（41–54） |
|---|---|---|---|---|---|---|
| **M1** | — | 模式 18（自文档化外包）、模式 20 | — | 模式 33（自纠重试不占预算）、模式 35 反例 B | — | 模式 41（预算 × 单次成本）、模式 53 反例（豁免通道不可审计） |
| **M2** | 模式 4（检查点位置：每波次都在循环内检查） | 模式 22（边界由扩展点定义，不可发现性消除） | — | 模式 33 | — | — |
| **M3** | 模式 8（换算比不是常数，`SubPlanBody` 把一整个子 Run 塞进一个节点） | — | — | — | 模式 38（守卫的排除集合） | — |
| **M4** | 模式 13、模式 14 | — | 模式 25（截断整批失败） | 模式 32（无上限退出有独立终态） | — | 模式 41（未知成本走默认预算，方向即判据）、模式 52 反例（终态命名比例 13/15，无上限退出没有自己的名字） |
| **M5** | 模式 17 | — | 模式 26（未知工具走错误结果，不抛） | — | — | — |
| **M6** | — | — | 模式 31（构造期跨字段不变量） | 模式 34（枚举与数字分开钳） | 模式 38（比较器共享默认值） | 模式 46（解析器处理 YAML 引号把 bool 变成字符串） |
| **M7** | 模式 13（正例与反例同一文件） | — | 模式 25（抢救解析器让截断参数通过校验） | — | — | 模式 42（确定性检测拒绝混合证据，签名含 provider） |
| **M8** | 模式 3 | — | 模式 26 | — | — | 模式 42（fail open 保留重试，代价写在注释里） |
| **M9** | 模式 1、模式 2、模式 9、模式 10 | 模式 23（跨字段不变量） | 模式 31（规范路径做队列键） | 模式 34（工具结果按提交顺序发出） | — | 模式 43（清零由消费方判）、模式 49（字段清单从签名派生） |
| **M10** | 模式 13 | — | 模式 25 | 模式 32 | — | 模式 43（记录与递增的时序：先记录再递增） |
| **M11** | 模式 15 | — | 模式 26、模式 27 | — | — | 模式 42（`all` 判定 + 证据签名维度） |
| **M12** | — | — | — | — | — | — |
| **M13** | 模式 6、模式 17 | 模式 24 反例 A（建议性 vs 强制层） | — | 模式 35 反例 C（不可覆盖的硬拒层） | — | 模式 48（删除授权绑定到确认时的身份）、模式 50（默认方向 loud） |
| **M14** | — | — | — | — | — | — |
| **L1** | 模式 4、模式 5、模式 7 | 模式 22 | — | 模式 32（三条终态缺的那一条） | 模式 36（信号兜底）、模式 37（终态编码是否启动） | 模式 52 反例（诊断字段存在但没人填）、模式 53 反例（豁免通道） |
| **L2** | — | 模式 18（谁负责补预算） | — | 模式 35 反例 A（两层默认值不一致） | — | 模式 54 反例（显式拒绝聚合，不是漏配） |
| **L3** | 模式 8（深度有正向实践，广度明确标为缺口） | — | — | — | 模式 38（守卫排除自身） | — |
| **L4** | 模式 7 | — | — | — | — | 模式 51（MoA 的终止判据仍是主 loop 的，不依赖模型自判） |
| **L5** | 模式 11、模式 12 | 模式 20、模式 21 | 模式 29（检查点是状态转移）、模式 31（状态依赖清理） | — | 模式 39（noContext 收尾） | 模式 47（观察型与已提交分两次发布）、模式 48（TOCTOU 绑定） |
| **L6** | 模式 16（以反例入册） | — | 模式 30（中断不重调模型） | — | 模式 39、模式 40（部分失败的状态树） | 模式 47（报告措辞匹配实际状态）、模式 50（部分失败编码可配置，看默认方向） |
| **L7** | 模式 3、模式 4、模式 13、模式 14 | 模式 19 | 模式 26、模式 27 | — | 模式 36、模式 37、模式 39 | 模式 44（窄分类器 + 加长退避）、模式 41（成本触发的预算收缩） |
| **L8** | 模式 15（策略机制正向，默认值反例） | 模式 19（五项要素齐全，含对称 jitter） | 模式 27（双层正则，非重试优先）、模式 28（两层溢出上限） | — | — | 模式 44（上限与退避表一起算，共用常量单一来源）、模式 45（jitter 种子并发去相关） |
| **L9** | — | — | — | — | — | 模式 50、模式 51（半锚点：降级可见 + 裁判是谁，缺冲突检测） |
| **L10** | 模式 3 | 模式 24 反例 C（无上限重试） | — | — | — | — |
| **X1** | 模式 10（`last_level` 暴露压缩级别） | 模式 19、模式 20 | 模式 27（重试是事件流） | 模式 34（钳制要可观测）、模式 35 反例 B（聚合器丢字段） | — | 模式 52 反例（观测面比例：13/15 终止路径同名）、模式 47（措辞分两级） |
| **X2** | 模式 1（波次 delta 事件溯源）、模式 13 | 模式 20、模式 21 | 模式 30（`recovery: true` 标记）、模式 29 | — | — | 模式 43（状态作用域可重建：清零由单一判据决定） |
| **X3** | 模式 6 | — | — | — | — | — |
| **X4** | — | — | — | — | — | 案例 6 的分层护栏（`_ATTENDED_PLATFORMS` 字面量集合 + `LoopCapConfig` 的开关分层，见案例 6 前言） |
| **X5** | 模式 5 | 模式 21、模式 23、模式 24 反例 B | 模式 31（构造期跨字段不变量） | 模式 34（非对称钳制） | 模式 38（默认值恰是无上限） | 模式 46（越界与翻转两半都要有清单）、模式 50（默认值是不可观测的降级）、 |

案例 3–5 那次扩充的收获**不是补空行**——此前 5 个空行里只补上了 **M6** 一个（模式 31 的构造期跨字段不变量、模式 34 的枚举与数字分开钳、模式 38 的比较器共享默认值）。案例 6（模式 41–54）又补上了 **X4** 一个：案例 6 的分层护栏——`_ATTENDED_PLATFORMS` 字面量集合 + `LoopCapConfig` 的开关分层，给出了「环境间差异由一个集合的边界决定、上限按受哪个开关控制分层」的可引写法（该锚点在案例 6 前言，不占模式编号）。

**空行没被填满这件事仍然是最强的结论**：M12（输出契约）、M14（不确定性与可复现）这两项在六个不同语言、不同领域（其中一个是非 LLM 的、一个是终端 agent 产品）的代码库里仍然找不到可引写法——缺失是系统性的，不是这几个项目偷懒。六个案例里没有任何一个把「输出是否符合契约」做成机器可判定的；也没有任何一个把温度 / 模型名 / 采样参数落盘到能重建一次运行的程度。

- **已记录的缺口**（有缺陷结论，只是没有好写法可引）：M12 无输出契约、L9 无跨 agent 冲突检测、M14 从不落盘温度与模型名、L10 的「挂起等人工」在六个案例间反复缺失。M6 与 L3 此前也空着，M6 已有锚点（见上表），L3 自案例 1 模式 8 起有锚点（深度有正向实践、广度明确标为缺口）。X4 原为空，案例 6 补上（模式 46、模式 54）。
- **案例 3–6 新增的判据**（此前没有对应条目的）：M7 的「抢救解析器让截断参数通过校验」三档判定（模式 25）、M11 的「同批分歧必须全批一致才终止」（模式 26）、L8 的「白名单内模式重叠时的优先级」与「计时 API 自身的上限」两问（模式 27、28）、M1/M2 的「自纠重试不占调用方预算」（模式 33）、X5 的「钳制要可观测」（模式 34）、M13 的「不可覆盖层与求值顺序」（模式 35 反例 C）、L1 的「可丢的唤醒信号需有限轮询兜底」（模式 36）、L3 的「守卫排除自身」（模式 38）、L5 的「收尾不继承触发它的取消令牌」（模式 39）、L6 的「部分失败必须编码进数据而非只进日志」（模式 40）、**M1 的「预算要乘上单次尝试成本」（模式 41）、M7/M11 的「确定性检测拒绝混合证据」（模式 42）、M9/M10 的「清零责任归消费方」（模式 43）、L8 的「上限与退避表一起算」（模式 44）、L8 的「jitter 种子并发去相关」（模式 45）、X5 的「越界与翻转两半都要有清单」（模式 46）、L5/L6 的「观察型与已提交分两次发布」（模式 47）、L5/M13 的「删除授权绑定到确认时的身份」（模式 48）、M9 的「字段清单从签名派生」（模式 49）、L6/X5 的「默认方向是判据」（模式 50）、L4/L9 的「裁判是谁要说清楚」（模式 51）、L1/X1 的反例「诊断字段存在但没人填」（模式 52）、L1/M1 的反例「豁免通道在本模块外」（模式 53）、L2/X5 的反例「显式拒绝聚合，不是漏配」（模式 54）**。

**不要把空行理解成「还没整理」——空行本身就是结论。尤其不要为了让挂载索引看起来完整而发明做法**：不变式表里已经写了它们缺什么会怎样，缺一个正向锚点不会让审计更弱，编造一个会。

（注：L10 有锚点但只有半个——模式 3 的 fail-fast 命名只解决了「死得响不响」，没解决「挂起等人工」和「验收器」两半；模式 24 反例 C 记录的是同一缺口的另一种形态。半锚点也是锚点，按原项判。）

（注：L9 现在也有半锚点——模式 50 的「降级要可见」和模式 51 的「裁判是谁」覆盖了 L9 的前两问，但 L9 判据的第三问「有没有一个分支会因为矛盾而停下来」没有锚点，所以仍按空项判。审计 L9 时这三个半锚点都要引。）

（注：案例 3 与案例 4 的 `stopReason: "length"` / `finish_reason` 判据互相印证，模式 25 与模式 32 应一起引——一个讲「截断时做什么」，一个讲「截断后如何结束」。）

（注：案例 6 的三条反例——模式 52、53、54——是「设计选择看起来像缺口」的形态：不是缺陷，但审计不能跳过，因为从外部看它们和缺陷长得一样，唯一区分依据是文档 / 注释里写没写出来。引的时候连同这个前提一起引，不要只引缺陷本身。）

---

## 用法

审计一个项目时：

1. 判 `GAP` / `RISK` 之后，查挂载索引找对应模式，把「做法 + 迁移要点」写进报告的修复建议里。建议要具体到「改哪个类型、加哪个字段」，不要写「引入声明式通道」。
2. 判 `OK` 时查 §0，确认判的是「结构性不可表达」还是「运行时检查」。
3. 判 `PENDING` 时不要引本文件——正向锚点不能替代核验。

本文件的锚点绑定 **2026-09-07**（案例 1–2）与 **2026-09-08**（案例 3–6）两份案例快照。**引锚点前先确认目标项目是否同一版本**；引「做法」不需要确认，引「行号」需要。

案例 2–6 都是活跃迭代的上游主分支（`master` / `main`），行号漂移快于案例 1。三类锚点要注意方向：案例 5 的锚点来自**非 LLM 项目**，引它时是在引「调度类系统的通用不变式」，不是引「agent 框架的做法」；案例 4 的多个锚点是**反例**（模式 35），引的时候要连同「可迁移的判据」一起引，不要只引缺陷本身；案例 6 的三条反例（模式 52、53、54）是「设计选择看起来像缺口」的形态，引的时候要连着「不是缺陷，但审计不能跳过」这个前提一起引。

案例 3–6 都是**定向抽取**，不是全仓库通读：取回与不变式相关的模块（18 个文件 / 案例 6），大文件（>70KB）只做头部通读 + 定向检索。所以「本案例没有某项做法」不等于「该项目没有」，等于「取回的这部分里没有」。**引锚点时不要写「本项目完全没有 X」，写「取回的部分里没找到 X」**——这两个断言的证据强度完全不同。
