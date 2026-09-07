# 报告模板

审计结束后生成两个文件，同名同目录：`loop-audit-report.md`（给人读）+ `loop-audit.json`（给 CI / 脚本读）。

---

## loop-audit-report.md 结构

### 1. 摘要（放最前，3 行内）

```
审计对象：<路径或仓库名>　　框架：<识别结果>　　审计时间：<ISO 8601>
结论：<BLOCK / PASS / PASS WITH WARNINGS>
阻断级 <N> 项　缺口 <N> 项　风险 <N> 项　不适用 <N> 项
```

判定规则：`blockers > 0` → `BLOCK`；`blockers == 0 && gaps > 0` → `PASS WITH WARNINGS`；否则 `PASS`。

### 2. 框架识别（必须显式写，这是后面所有结论的前提）

```
声明版本：<来自哪个清单文件 + 版本>
源码使用：<哪些 import 命中，file:line>
自省结果：<成功 / 失败 + 原因>
结论：<框架名> <版本>，证据等级 [自省实测] / [源码确认]
```

识别结果为「无框架（自研 loop）」时，明确写出：

```
结论：自研 loop，无框架兜底
依据：<哪些 LLM client import + 哪些循环结构>
影响：M1-M14 全部需自查，无一项自动满足
```

识别结果为「框架未覆盖」时：

```
结论：识别到 <框架名>，但差异矩阵未收录
影响：仅执行不变式层（M1-M14 / L1-L10 / X1-X5），跳过框架差异映射
建议：按 framework-map.md 末尾模板补录
```

### 3. 发现（**只写有问题或有风险的项**）

每项固定四段，不要省略证据：

```markdown
#### M1 · 硬预算熔断  [GAP]
**现象**：<一段话说清楚缺什么，不引用 pattern 名>
**证据**：`src/agent.py:42` — `<关键代码片段，5 行内>`
**后果**：<具体到会发生什么，不写「可能导致问题」这类空话>
**建议**：<具体到改什么，能给代码就给>
```

`证据` 段没有行号的结论不允许出现——写不出证据的，降级为「需要确认」并标注 `[待确认]`。

### 4. 框架差异结论（逐框架一段，只列非「框架已保证」的格子）

```markdown
### LangGraph 特有的坑
- **M3 换算比**：上限数的是 step（Pregel superstep），一个 step 是「计划→并行执行→更新」的完整三阶段，内部可含多个并行节点。单线路径下 1 step ≈ 1 次节点执行，并行分支下 1 step 可含 N 次 → 你的 N 不能直接当轮数用
- **M9 并发写会炸**：reducer 覆盖语义（`LastValue`）+ 并行分支写同一 key → 抛 `INVALID_CONCURRENT_GRAPH_UPDATE`，整个 step 失败（可用性故障，不是数据丢）。真正的静默风险在「无 reducer 的默认 channel 就是覆盖语义，单节点写会无声覆盖」
```

**这一段必须引用 `references/framework-map.md` 里对应条目的证据等级**——矩阵已核验的条目（标注 `[源码确认]`/`[官方文档确认]`）可以直接写成结论；未核验的（AutoGen/AG2、deepagents）只能写成「需要确认」。不要把矩阵里的 `[待确认]` 升级成项目缺陷。

「框架已保证」的格子不占篇幅，但 JSON 里要计数。

### 5. 待确认清单

所有 `[待确认]` 项集中列在这，每条写明「要确认什么」和「怎么确认」（自省代码或读哪个文件）。这是自省失败时的诚实出口，**不要藏起来**。

### 6. 建议修复顺序

按「阻断级 → 一行能改的 → 需要重构的」排序，不要按 M/L 编号顺序排。

---

## loop-audit.json 结构

```json
{
  "skill": "loop-engineering-check",
  "skill_version": "1.5.0",
  "generated_at": "2026-09-07T06:10:00Z",
  "target": "path/to/project",
  "framework": {
    "declared": ["langgraph==0.2.55"],
    "used": ["langgraph"],
    "introspected": true,
    "self_built_loop": false,
    "uncategorized": []
  },
  "findings": [
    {
      "id": "M1",
      "layer": "M",
      "title": "硬预算熔断",
      "verdict": "GAP",
      "severity": "blocker",
      "evidence": [
        {
          "path": "src/agent.py",
          "line": 42,
          "snippet": "result = agent.invoke(input)",
          "confidence": "source"
        }
      ],
      "recommendation": "在 invoke 配置中显式设置上限并确认计数单位",
      "framework_note": "LangGraph 的 upper-limit 数节点执行，换算比约 2:1"
    }
  ],
  "summary": {
    "blockers": 0,
    "gaps": 0,
    "risks": 0,
    "ok": 0,
    "na": 0,
    "pending_confirmation": 0
  },
  "confidence": "introspected"
}
```

**字段约束**：

| 字段 | 取值 | 说明 |
|---|---|---|
| `verdict` | `OK` / `GAP` / `RISK` / `N/A` / `PENDING` | `GAP` = 缺失；`RISK` = 存在但有框架特有风险；`PENDING` = 无法核验 |
| `severity` | `blocker` / `major` / `minor` | 只有 `blocker` 计入 `summary.blockers` |
| `evidence.confidence` | `introspected` / `source` / `official_doc` / `version_knowledge` / `pending` | 与报告里的证据等级五档一一对应：`[自省实测]` / `[源码确认]` / `[官方文档确认]` / `[版本知识]` / `[待确认]` |
| `confidence`（顶层） | `introspected` / `declared_only` / `grep_only` | 整个审计的证据等级，自省失败时降一级 |

**CI 用法**：

```bash
python -c "import json,sys; sys.exit(1 if json.load(open('loop-audit.json',encoding='utf-8'))['summary']['blockers']>0 else 0)"
```

`encoding='utf-8'` 不是装饰：`open()` 不带 encoding 时用平台默认编码，在 GBK/cp1252 环境下这段命令会对**任何**审计结果抛 `UnicodeDecodeError` 并以 `exit=1` 退出——一次通过的审计也会被它判成 BLOCK，且报错里看不出原因。本机（Windows cp936）已复现。

---

## 输出纪律

1. **不写「无问题」的项**到 Markdown，只在 JSON 计数。报告是给要动手的人读的，篇幅用在有动作的地方。
2. **不引用 pattern 名当结论。** `static-hints.py` 的输出是候选证据；写进报告的必须是读过的源码 + 行号。
3. **不猜 API 名。** 自省没跑通的，写「需确认 <机制> 的参数名」，不要写具体的参数名。
4. **后果必须具体。** 禁止「可能导致 agent 行为异常」这类句子——要写「超限后整个 invoke 抛异常，上层拿到空结果，重试循环把它当业务失败再调一次 LLM，成本翻倍」。
