# loop-engineering-check

一个 Claude Code / Codex CLI / Cursor 等 AI IDE 可用的 Skill，用来审计任何 AI Agent 项目：**面对 LLM 的不确定性，这个项目的 loop 在哪些地方没有设防，一旦模型走偏会怎样？**

只读审计——不修改被审计项目的任何文件。

## 为什么需要它

用 Agent 框架写应用时，微观 Agent Loop 层的安全保障往往被遗漏：硬预算熔断（`max_turns` / `max_cost`）、工具幻觉拦截、死循环检测、状态完整性……而宏观 Loop Engineering 层同样有漏洞：外层循环无上限、预算没下传到子 Agent、失败收敛策略缺失。

最反直觉的是：**框架帮你兜底的部分，往往是错的方式。** 比如 smolagents 超限时不会报错，而是再调一次 LLM 合成一个看起来完整的最终答案，正常返回——你只写 `try/except` 根本感知不到。这类行为只有逐框架核验过才知道，凭记忆写框架结论会错，而且错的方式往往和直觉相反。

## 检查范围

三层共 29 项：

| 层 | 项数 | 关注 |
|---|---|---|
| **M 层** 微观 Agent Loop | 14 | 框架层：预算熔断、计数单位换算、工具注册表强校验、参数 schema、超限时行为、并发写状态 |
| **L 层** 宏观 Loop Engineering | 10 | 外层编排边界、预算下传、委派深度、失败收敛、副作用幂等、验收与 Goodhart 防护 |
| **X 层** 跨层 | 5 | 可观测性、可重放、人机交接、环境一致性、配置漂移 |

覆盖的框架：**LangGraph、LangChain、deepagents、Google ADK、Crawl4AI、OpenAI Agents SDK、AutoGen/AG2、LlamaIndex、smolagents**，以及**自研 while 循环**（这是主路径而非例外——真实项目里裸循环的比例通常高于用框架的）。

每个框架每一项给出四选一判定：`框架已保证` / `默认不安全需自加` / `框架特有风险` / `不适用`。

## 自动识别框架

不猜，走三层证据：

1. **声明** — `pyproject.toml` / `requirements*.txt` / `poetry.lock` / `uv.lock` / `package.json` 等
2. **使用** — 源码 import（声明 ≠ 使用，只在清单里没被 import 的记为「未使用」，不参与差异矩阵）
3. **自省** — 本机 introspect 已安装包的真实签名（API 名从用户机器抓的，不是从记忆背的）

`scripts/static-hints.py` 是只读静态扫描器，产出**候选证据**（行号 + pattern），供语义审计确认或排除。扫描是保守的：命中 ≠ 违规，报告里禁止直接引用 pattern 名当结论。双向都要看：**无命中 ≠ 通过**——实测只覆盖 7 项不变式（M1/M5/M8/M9/M11/M14/L8），19 项脚本给不出信号、必须靠逐行阅读；输出里的 `scanner_coverage` 字段就是这份检出能力声明，报告要照它区分「查了、没有」和「没查」。第三方/研究副本目录（`research`、`examples`、`vendor` 等）默认跳过，用 `--scan-vendor` 打开。

## 证据等级

五级，从高到低。这是这个技能最重要的部分：

| 等级 | 来源 |
|---|---|
| `[自省实测]` | 本机 introspect，与官方文档并列最高 |
| `[源码确认]` | 上游 file:line |
| `[官方文档确认]` | URL + 页面所述版本 |
| `[版本知识]` | 未核验，标注版本 |
| `[待确认]` | 无法核验，不做断言 |

矩阵里 **10 处早期结论被核验推翻**，全部就地标了 `⚠ 早期版本写 X 是错的`。保留这些错误记录是刻意的——它们证明凭记忆写的框架结论会错。

**未核验的部分保持 `[待确认]`**：AutoGen/AG2 整表（三代 API 语义不同，AG2 主分支已把 GroupChat 迁到 `network` 抽象）、deepagents 继承项。核验深度不均，引用矩阵时先看该格的证据等级与核验日期。

## 安装

把整个 `loop-engineering-check/` 目录放进你的技能目录：

```bash
# Claude Code
cp -r loop-engineering-check ~/.claude/skills/

# Codex CLI
cp -r loop-engineering-check ~/.codex/skills/

# 或直接用 git
git clone https://github.com/yejinlei/loop-engineering-check ~/.claude/skills/loop-engineering-check
```

依赖：Python 3.8+（`static-hints.py` 只用标准库，无第三方包）。

## 使用

在 AI IDE 里直接触发，比如：

> 「检查一下这个项目的 agent loop 有没有跑飞的风险」
> 「帮我审计这个 agent 应用的 loop engineering」
> 「agent 跑飞、工具幻觉、预算熔断、max_turns 有没有设好」

或者显式调用 `/loop-engineering-check`。

## 输出

两个文件，同名同目录：

- `loop-audit-report.md` — 给人读。只写有问题或有风险的项，每项固定四段（现象 / 证据 / 后果 / 建议），证据必须带 `file:line`
- `loop-audit.json` — 给 CI 读。`summary.blockers` 可直接卡 CI：

```bash
python -c "import json,sys; sys.exit(1 if json.load(open('loop-audit.json',encoding='utf-8'))['summary']['blockers']>0 else 0)"
```

`encoding='utf-8'` 不是装饰：`open()` 不带 encoding 用平台默认编码，在 GBK/cp1252 环境下这段命令会对**任何**审计结果抛 `UnicodeDecodeError` 并以 `exit=1` 退出——一次通过的审计也会被它判成 BLOCK，且报错里看不出原因。

判定规则：`blockers > 0` → `BLOCK`；`blockers == 0 && gaps > 0` → `PASS WITH WARNINGS`；否则 `PASS`。

## 目录结构

```
loop-engineering-check/
├── SKILL.md                     # 技能主体（234 行，含 29 项速览表 + M/L/X 三层详表与正向判据）
├── README.md                    # 本文件
├── LICENSE
├── references/
│   ├── framework-map.md         # 8 个框架矩阵 + LangChain（不占独立矩阵）+ 自研 loop（641 行，带目录）
│   ├── loop-engineering-patterns.md  # 正向实践库：6 个案例、54 条做法（1635 行）
│   └── report-template.md       # 报告与 JSON 结构模板
└── scripts/
    └── static-hints.py          # 只读静态提示扫描器（标准库，681 行）
```

## 实践来源

这个技能的判据不是凭空写的，全部来自逐行核验过的真实代码。分三类。

### 六个案例工程（正向实践库，见 `references/loop-engineering-patterns.md`）

| 案例 | 来源 | 核验内容 |
|---|---|---|
| 案例 1 · 自研 BSP 波次编排引擎 | 匿名（本地教学代码库，按用户要求脱敏） | 2026-09-07 逐行读源码，模式 1–17（含 6 条缺陷反例） |
| 案例 2 · [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | 公开仓库（TypeScript，Cordis 插件框架） | 2026-09-07 读上游 `master` 源码，模式 18–24（含 3 条反例） |
| 案例 3 · [pi](https://github.com/earendil-works/pi) | 公开仓库（TypeScript，通用 agent 运行时） | 2026-09-08 读上游 `main` 源码，模式 25–31 |
| 案例 4 · [OpenHarness](https://github.com/HKUDS/OpenHarness) | 公开仓库（Python） | 2026-09-08 读上游 `main` 源码，模式 32–35（含 3 条反例） |
| 案例 5 · [harness/harness](https://github.com/harness/harness) | 公开仓库（Go，**非 LLM** 的 CI/CD 平台） | 2026-09-08 读上游 `main` 源码，模式 36–40（含 1 条反例） |
| 案例 6 · [hermes-agent](https://github.com/NousResearch/hermes-agent) | 公开仓库（Python，面向终端用户的完整 agent 产品，CLI / TUI / desktop / API server） | 2026-09-08 读上游 `main` 源码，定向取回 18 个文件，模式 41–54（含 3 条反例） |

六个案例互补而非互证：案例 1 在状态与循环控制上最扎实、预算与重试全空；案例 2 在预算与重试上最扎实、却自文档化了「无内建轮次预算」；案例 3 在流式完整性与中断处理上最扎实；案例 4 在引擎预算与 CI 等待上最完整、但它多个锚点是**反例**；案例 5 是非 LLM 的调度系统，引它是在引「调度类系统的通用不变式」——但它反过来证明不变式集合是领域通用的，不是 LLM 特有的；案例 6 是终端 agent 产品，多数锚点是「同一套不变式在不同运行模式下的分层」。

**这不是行业基线，是六个项目的实践。** 引用时要说明这一点，尤其引 M1/L2 的锚点时——那是「显式外包」的设计选择，不是缺陷已修。

挂载索引里 **3 项仍无锚点**（M12 输出契约、M14 不确定性与可复现、L9 跨 agent 一致性），L9 与 L10 各只有半个锚点。L9 的半锚点证明「降级要可见」与「裁判是谁」，不证明「冲突能被检出」——那是 L9 判据的核心，所以按原项判仍记为空。这三个空项横跨六个不同语言、其中一个是非 LLM 项目、一个是终端 agent 产品，找不到可引写法是系统性的——这本身就是结论。

案例 1 之所以脱敏：它是被审计对象，不是学习来源，公开其名称没有信息增益。案例 2–6 是被学习对象，署名才有信息增益——需要复核做法时能直接去对源码。案例 2–6 都是活跃迭代的上游主分支，引行号前需要确认目标项目是否同一版本；引「做法」不需要。案例 3–6 是**定向抽取**（取回与不变式相关的模块），不是全仓通读——所以「本案例没有某项做法」等于「取回的部分里没找到 X」，不等于「该项目完全没有 X」。

### 九个框架的官方文档与上游源码（差异矩阵，见 `references/framework-map.md`）

九项里八项有完整矩阵；**LangChain 不占独立矩阵**（旧式 agent 抽象，loop 语义已让位给 LangGraph，只单列了 `AgentExecutor` 的换算比这一处例外）。

每个框架每一项的判定都标了证据等级与核验日期。核验于 2026-09-07 **推翻了 10 处早期结论**，全部就地保留为 `⚠ 早期版本写 X 是错的`——保留错误记录是刻意的，它们证明凭记忆写的框架结论会错，且错的方式往往和直觉相反。

已核验：LangGraph（最扎实）、OpenAI Agents SDK、Google ADK、smolagents、LlamaIndex、Crawl4AI。未核验、结论一律 `[待确认]`：AutoGen/AG2 整表（三代 API 语义不同）、deepagents（仅继承 LangGraph）。

### 学习方法的四条规则

这些比具体做法更耐用：

1. **正向判据是准入条件，不是加分项。** 只避免负面漏点、但达不到正向判据的，判 `RISK` 不判 `OK`。
2. **判效果，不判写法。** 目标项目用别的方式达到同一保证效果，同样算通过。
3. **反例入册。** 同一个案例里的好写法和缺陷一起记——「重试机制设计得好、默认值却很糙」这类结论只有正反对照才出得来。
4. **空行本身就是结论。** 挂载索引里有 3 项没有正向锚点（M12、M14、L9），L9 与 L10 各只有半个锚点——L9 的半锚点证明「降级要可见」与「裁判是谁」，不证明「冲突能被检出」，那是 L9 判据的核心，所以按原项判仍记为空。这是这些案例在这几项上确实没有可引的做法；为了填满索引而发明做法是造假。六个案例横跨六个不同语言、其中一个是非 LLM 项目、一个是终端 agent 产品，都找不到写法——缺失是系统性的。

## 已知局限

1. **自省只给签名，不给语义。** 「LangGraph 并发写非 reducer key 会抛异常而不是丢数据」这类行为自省不会告诉你，只能来自已核验的官方文档/源码。
2. **框架表外的框架只跑不变式层**，不给框架特有风险，不编造 API 名。
3. **不同 harness 执行深度不同。** 无 shell 或无目标虚拟环境时自省失败，降级为「声明版本 + 源码 grep」，证据等级随之下降并在报告中说明。
4. **已核验 ≠ 永久正确。** 框架迭代很快，引用矩阵时先看该格的证据等级与核验日期，别把某一年的结论当成永恒事实。
5. **正向实践库是六个项目的实践，不是行业基线。** 案例间互补而非互证，且有 3 项仍无正向锚点（M12、M14、L9），L9/L10 为半锚点。详见 SKILL.md「已知局限」第 6 条。

## 贡献

新增框架：按 `references/framework-map.md` 末尾模板填三样——不变式映射（M/L/X 每项四选一）、自省代码片段、已知语义陷阱（标注版本）。

## License

MIT
