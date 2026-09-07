# Loop Engineering 优秀实践库（正向锚点）

本文件是 `SKILL.md` 不变式表的**正向面对**：不变式表回答「缺什么会怎样」，本文件回答「做得好的长什么样、怎么搬过去」。

做法来自**两个真实案例**，全部锚点均为 `[源码确认]`，核验日期 **2026-09-07**。没有一条是设计意图推测。

| | 案例 1 · 自研 BSP 波次编排引擎 | 案例 2 · deepseek-harness |
|---|---|---|
| 定位 | 匿名（按用户要求脱敏）的本地教学代码库 | [公开仓库](https://github.com/deepseek-ai/deepseek-harness)，TypeScript，构建在 Cordis 插件框架上 |
| 登记 | `references/framework-map.md`「自研 loop」一节 | 本文件「案例 2」一节 |
| 产出 | 模式 1–17：11 条做法 + 6 条缺陷反例 | 模式 18–24：4 条做法 + 3 条反例 |
| 强项 | 状态合并规则、循环终态区分、审批分层 | 重试退避、配置不变量、事件持久化 |

两个案例互补：案例 1 在状态与循环控制上最扎实，但在预算与重试上全空；案例 2 在预算与重试上最扎实，却**自文档化**了「无内建轮次预算」。**「没有内建预算」不是批评，是设计选择**——它写在 Known Limitations 里，所以是反例（可参照的边界），不是缺陷指控。

**引法有两条，要求不同：** 引「做法」可以直接迁移，不需要版本确认；引「行号」要先确认目标项目是否同一版本——案例 1 是小型教学代码库，案例 2 是活跃迭代的上游 `master`，两者的行号都会在迭代中漂移。

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

## 挂载索引：不变式 → 模式

| 不变式 | 案例 1（模式 1–17） | 案例 2（模式 18–24） |
|---|---|---|
| **M1** | — | 模式 18（自文档化外包）、模式 20 |
| **M2** | 模式 4（检查点位置：每波次都在循环内检查） | 模式 22（边界由扩展点定义，不可发现性消除） |
| **M3** | 模式 8（换算比不是常数，`SubPlanBody` 把一整个子 Run 塞进一个节点） | — |
| **M4** | 模式 13、模式 14 | — |
| **M5** | 模式 17 | — |
| **M6** | — | — |
| **M7** | 模式 13（正例与反例同一文件） | — |
| **M8** | 模式 3 | — |
| **M9** | 模式 1、模式 2、模式 9、模式 10 | 模式 23（跨字段不变量） |
| **M10** | 模式 13 | — |
| **M11** | 模式 15 | — |
| **M12** | — | — |
| **M13** | 模式 6、模式 17 | 模式 24 反例 A（建议性 vs 强制层） |
| **M14** | — | — |
| **L1** | 模式 4、模式 5、模式 7 | 模式 22 |
| **L2** | — | 模式 18（谁负责补预算） |
| **L3** | 模式 8（深度有正向实践，广度明确标为缺口） | — |
| **L4** | 模式 7 | — |
| **L5** | 模式 11、模式 12 | 模式 20、模式 21 |
| **L6** | 模式 16（以反例入册） | — |
| **L7** | 模式 3、模式 4、模式 13、模式 14 | 模式 19 |
| **L8** | 模式 15（策略机制正向，默认值反例） | 模式 19（五项要素齐全，含对称 jitter） |
| **L9** | — | — |
| **L10** | 模式 3 | 模式 24 反例 C（无上限重试） |
| **X1** | 模式 10（`last_level` 暴露压缩级别） | 模式 19、模式 20 |
| **X2** | 模式 1（波次 delta 事件溯源）、模式 13 | 模式 20、模式 21 |
| **X3** | 模式 6 | — |
| **X4** | — | — |
| **X5** | 模式 5 | 模式 21、模式 23、模式 24 反例 B |

两个案例合计补上了案例 1 的四个空行：**M1、L2、L8**（三个都有正向实践）、**L5**（案例 1 已有，案例 2 补了事件侧）。**仍然空的 4 行：M6、M12、L9、X4**——两个案例在这四项上都无可引做法。

- **已记录的缺口**（有缺陷结论，只是没有好写法可引）：M6 无参数类型校验、M12 无输出契约、L9 无冲突检测、M14 从不落盘温度与模型名、X4 无配置分层。
- **案例 1 已补上的空行**：M1 与 L2 现在有案例 2 的锚点，但都是「显式外包」形态——引它们时要说明这是设计选择，不是缺陷已修。

**不要把空行理解成「还没整理」——空行本身就是结论。尤其不要为了让挂载索引看起来完整而发明做法**：不变式表里已经写了它们缺什么会怎样，缺一个正向锚点不会让审计更弱，编造一个会。

（注：L10 有锚点但只有半个——模式 3 的 fail-fast 命名只解决了「死得响不响」，没解决「挂起等人工」和「验收器」两半；模式 24 反例 C 记录的是同一缺口的另一种形态。半锚点也是锚点，按原项判。）

---

## 用法

审计一个项目时：

1. 判 `GAP` / `RISK` 之后，查挂载索引找对应模式，把「做法 + 迁移要点」写进报告的修复建议里。建议要具体到「改哪个类型、加哪个字段」，不要写「引入声明式通道」。
2. 判 `OK` 时查 §0，确认判的是「结构性不可表达」还是「运行时检查」。
3. 判 `PENDING` 时不要引本文件——正向锚点不能替代核验。

本文件的锚点绑定 2026-09-07 案例快照。**引锚点前先确认目标项目是否同一版本**；引「做法」不需要确认，引「行号」需要。
