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

覆盖的框架：**LangGraph、deepagents、Google ADK、Crawl4AI、OpenAI Agents SDK、AutoGen/AG2、LlamaIndex、smolagents**，以及**自研 while 循环**（这是主路径而非例外——真实项目里裸循环的比例通常高于用框架的）。

每个框架每一项给出四选一判定：`框架已保证` / `默认不安全需自加` / `框架特有风险` / `不适用`。

## 自动识别框架

不猜，走三层证据：

1. **声明** — `pyproject.toml` / `requirements*.txt` / `poetry.lock` / `uv.lock` / `package.json` 等
2. **使用** — 源码 import（声明 ≠ 使用，只在清单里没被 import 的记为「未使用」，不参与差异矩阵）
3. **自省** — 本机 introspect 已安装包的真实签名（API 名从用户机器抓的，不是从记忆背的）

`scripts/static-hints.py` 是只读静态扫描器，产出**候选证据**（行号 + pattern），供语义审计确认或排除。扫描是保守的：命中 ≠ 违规，报告里禁止直接引用 pattern 名当结论。

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
python -c "import json,sys; sys.exit(1 if json.load(open('loop-audit.json'))['summary']['blockers']>0 else 0)"
```

判定规则：`blockers > 0` → `BLOCK`；`blockers == 0 && gaps > 0` → `PASS WITH WARNINGS`；否则 `PASS`。

## 目录结构

```
loop-engineering-check/
├── SKILL.md                     # 技能主体（171 行，M/L/X 三层检查项全表）
├── README.md                    # 本文件
├── LICENSE
├── references/
│   ├── framework-map.md         # 9 个框架 + 自研 loop 的差异矩阵（563 行，带目录）
│   └── report-template.md       # 报告与 JSON 结构模板
└── scripts/
    └── static-hints.py          # 只读静态提示扫描器（标准库）
```

## 已知局限

1. **自省只给签名，不给语义。** 「LangGraph 并发写非 reducer key 会抛异常而不是丢数据」这类行为自省不会告诉你，只能来自已核验的官方文档/源码。
2. **框架表外的框架只跑不变式层**，不给框架特有风险，不编造 API 名。
3. **不同 harness 执行深度不同。** 无 shell 或无目标虚拟环境时自省失败，降级为「声明版本 + 源码 grep」，证据等级随之下降并在报告中说明。
4. **已核验 ≠ 永久正确。** 框架迭代很快，引用矩阵时先看该格的证据等级与核验日期，别把某一年的结论当成永恒事实。

## 贡献

新增框架：按 `references/framework-map.md` 末尾模板填三样——不变式映射（M/L/X 每项四选一）、自省代码片段、已知语义陷阱（标注版本）。

## License

MIT
