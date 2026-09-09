#!/usr/bin/env python3
"""Agent loop 静态提示扫描器 — 只产生候选证据，不做判定。

用法:
    python static-hints.py <目标目录或单个文件> [--format json|text]
                           [--max-file-kb 512] [--exclude <名字或前缀> ...]

--exclude 可重复，按目录名或文件名前缀匹配，从根开始裁剪——所以排除 site-packages
这类子目录会拦住它下面所有层级：
    python static-hints.py ./proj --exclude site-packages --exclude .mypy_cache

设计原则:
  1. 只读。不 import 目标项目的任何模块，只用 ast 静态解析 + 正则。
  2. 保守。一个 hint 命中 != 违规。输出的是「值得 LLM 去读上下文」的行号，
     不是结论。报告里禁止直接引用 pattern 名当结论。
  3. 不猜。框架 API 名不做断言，只报「这里出现了 <名字>，见 file:line」。

能覆盖的 id:  M1 M2 M5 M6 M8 M9 M11 M14 / L8 / X5
纯判断层、脚本给不出候选证据的 id（跳过，交给语义审计）:
  M3 M4 M7 M10 M12 M13 / L1 L2 L3 L4 L5 L6 L7 L9 L10 / X1 X2 X3 X4
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", "out", "site-packages", ".egg-info", "coverage",
    "venv3", "envs", "virtualenv", "__pypackages__",
}
# 以点开头的目录名一律不扫——.venv3 / .venv312 / .conda 这类版本化虚拟环境
# 不需要逐个列名，startswith 已经拦住。
PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

# 第三方/研究副本目录名。审 agent loop 时它们不是被审计对象——实测
# deepseek_det2 的 research/minirag_src 是被拷进来的上游 MiniRAG、skills/ 是
# 外部技能包，两者贡献了 24 条 error_swallowed、6 条 LLM client、111 条
# timeout 命中，全部是别人项目的代码。默认不扫，用 --scan-vendor 打开。
VENDOR_DIRS = {
    "research", "vendor", "third_party", "thirdparty", "external", "ext",
    "examples", "example", "samples", "sample", "contrib", "deps",
    "sandbox", "playground", "spike", "experiment", "experiments",
}

# 预算/limiter 关键词，分两层。M1 的判据是「循环有没有上限」，
# 但 timeout 属于传输层设置，不是循环预算——实测 deepseek_det2 的 626 条
# limiter 命中里 451 条（72%）就是 timeout，把真正相关的 recursion_limit /
# max_turns 全部淹没。所以分成两个正则、两个 tier 输出，而不是一个扁平清单。
LOOP_BUDGET_KW = re.compile(
    r"""max_turns|max_iterations|max_iter|max_steps|max_retries|max_retry
        |max_loop|loop_limit|recursion_limit|max_executions
        |max_seconds|max_duration|max_waves|max_concurrency
        |budget|cost_limit|max_cost
    """,
    re.VERBOSE | re.IGNORECASE,
)
TOKEN_KW = re.compile(r"max_tokens|max_output_tokens|max_completion_tokens", re.IGNORECASE)
# 行级扫描用这一个正则、一次 pass 覆盖三层——拆成三个正则会多扫两遍全文。
_LINE_LIMITER_KW = re.compile(
    r"(?P<transport>timeout|deadline)"
    r"|(?P<token>max_tokens|max_output_tokens|max_completion_tokens)"
    r"|(?P<budget>max_turns|max_iterations|max_iter|max_steps|max_retries|max_retry"
    r"|max_loop|loop_limit|recursion_limit|max_executions"
    r"|max_seconds|max_duration|max_waves|max_concurrency"
    r"|budget|cost_limit|max_cost)",
    re.IGNORECASE,
)

RETRY_NAME = re.compile(r"retry|attempt|tries|retries", re.IGNORECASE)
BACKOFF_KW = re.compile(r"sleep|backoff|exponential|jitter", re.IGNORECASE)
SEED_KW = re.compile(r"\bseed\b|random_seed|set_seed", re.IGNORECASE)
TEMP_KW = re.compile(r"temperature\s*=", re.IGNORECASE)
ERROR_KIND_KW = re.compile(r"isinstance\(|\.status_code|errno|error_type", re.IGNORECASE)

FRAMEWORK_IMPORTS = {
    "langgraph": re.compile(r"^\s*(?:from\s+langgraph|import\s+langgraph\b)"),
    "deepagents": re.compile(r"^\s*(?:from\s+deepagents|import\s+deepagents\b)"),
    "google-adk": re.compile(r"^\s*(?:from\s+google\.adk|import\s+google\.adk\b)"),
    "crawl4ai": re.compile(r"^\s*(?:from\s+crawl4ai|import\s+crawl4ai\b)"),
    "langchain": re.compile(r"^\s*(?:from\s+langchain|import\s+langchain\b)"),
    "openai-agents": re.compile(r"^\s*(?:from\s+agents\b|import\s+agents\b)"),
    "pydantic-ai": re.compile(r"^\s*(?:from\s+pydantic_ai|import\s+pydantic_ai\b)"),
    "crewai": re.compile(r"^\s*(?:from\s+crewai|import\s+crewai\b)"),
    # 矩阵已收录，但覆盖深度标 [版本知识]
    "autogen-ag2": re.compile(r"^\s*(?:from\s+autogen_agentchat|import\s+autogen_agentchat\b"
                              r"|from\s+autogen\b|import\s+autogen\b"
                              r"|from\s+ag2\b|import\s+ag2\b)"),
    "llama-index": re.compile(r"^\s*(?:from\s+llama_index|import\s+llama_index\b"
                              r"|from\s+llama-index|import\s+llama_index\.core\b)"),
    "smolagents": re.compile(r"^\s*(?:from\s+smolagents|import\s+smolagents\b)"),
}
RAW_LLM_MODULES = ("openai", "anthropic", "litellm", "cohere", "mistralai", "ollama")
RAW_LLM_CLIENTS = re.compile(r"^\s*(?:from\s+(%s)\b|import\s+(%s)\b)" % ("|".join(RAW_LLM_MODULES), "|".join(RAW_LLM_MODULES)))

# JS/TS 侧的等价扫描（正则，够用即可，深度低于 Python 侧）
JS_HINTS = [
    ("M1", "loop_without_limiter", re.compile(r"while\s*\(\s*(?:true|1)\s*\)")),
    ("M8", "bare_catch", re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")),
    ("L8", "retry_without_backoff", re.compile(r"for\s*\(.*(?:retry|attempt).*\{")),
    ("M14", "temperature_without_seed", re.compile(r"temperature\s*[:=][^,}]{0,40}")),
]


def _scanner_coverage():
    """声明本脚本能产出提示的不变式 ID。

    从本模块自己的源码里抓 Hint(...) 与 result.add(...) 的第一个实参推导，
    不用手写清单——清单会漂移，而推导跟着代码走。这是「能检到什么」的
    唯一声明：报告用它算覆盖率，避免把「没检出」误读成「这项通过」。

    注意这是能力声明，不是本次命中统计：M9 有能力但零命中，仍算覆盖。
    """
    try:
        src = Path(__file__).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ids = set()
    for m in re.finditer(r"(?:Hint\(|result\.add\(\s*Hint\()\s*[\"']([MLX]\d+)[\"']", src):
        ids.add(m.group(1))
    return sorted(ids)


# 只对部分不变式有检出能力；其余项必须靠逐行阅读，脚本产出「无提示」
# 不等于该项通过。
SCANNER_HINT_IDS = _scanner_coverage()
# 只提供线索、不构成判定的信号来源：limiters → M2 校验点位置；
# framework_imports → M3 换算比；raw_llm_clients → L2 循环类型。
# 这三项故意避开 hint_ids——context 和 hint 要是重叠，7+4+19 就对不上 29，
# 读者按分类计数会算错。M1 虽然也拿到 raw_llm_clients 的线索，但它是 hint
# 级覆盖，归 hint_ids；raw_llm_clients 对它只是「无框架兜底」的补充线索。
SCANNER_CONTEXT_IDS = sorted({"M2", "M3", "L2"})

# 完整不变式骨架：M1-M14 / L1-L10 / X1-X5，共 29 项。
# 从字符串派生而不是手写 29 个 ID，这样骨架变更时推导跟着走。
ALL_INVARIANT_IDS = sorted(
    ["M%d" % i for i in range(1, 15)] + ["L%d" % i for i in range(1, 11)]
    + ["X%d" % i for i in range(1, 6)])

# 三类划分必须互斥且合计 = 全骨架，否则报告里的覆盖率算不出来。
# 校验放在 main() 的 JSON 分支（写一条 errors 标记 scanner_coverage 不可信），
# 不放这里：import 时 assert 会让整个脚本无输出，而报告其余部分是可信的——
# 宁可交出一份标注了「覆盖率不可信」的数据，也不要一份空报告。
SCANNER_NO_SIGNAL_IDS = sorted(
    set(ALL_INVARIANT_IDS) - set(SCANNER_HINT_IDS) - set(SCANNER_CONTEXT_IDS))


@dataclass
class Hint:
    id: str
    path: str
    line: int
    pattern: str
    code: str
    note: str = ""


@dataclass
class ScanResult:
    scanned_files: int = 0
    skipped_files: int = 0
    skipped_reasons: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    framework_imports: dict = field(default_factory=dict)
    raw_llm_clients: dict = field(default_factory=dict)
    limiter_mentions: list = field(default_factory=list)
    hints: list = field(default_factory=list)
    _fw_seen: set = field(default_factory=set)
    _cl_seen: set = field(default_factory=set)

    def note_framework(self, name: str, path: str, line: int):
        # AST 层和行级正则会重复命中同一个 import，去重
        key = (name, path, line)
        if key in self._fw_seen:
            return
        self._fw_seen.add(key)
        self.framework_imports.setdefault(name, []).append({"path": path, "line": line})

    def note_client(self, name: str, path: str, line: int):
        key = (name, path, line)
        if key in self._cl_seen:
            return
        self._cl_seen.add(key)
        self.raw_llm_clients.setdefault(name, []).append({"path": path, "line": line})

    def note_limiter(self, text: str, path: str, line: int, tier: str = "loop_budget"):
        # tier 分层输出，避免传输层 timeout 淹没循环预算命中
        self.limiter_mentions.append(
            {"text": text, "path": path, "line": line, "tier": tier})

    def add(self, hint: Hint):
        self.hints.append(hint)


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def setup_stdout() -> None:
    """重定向时强制 UTF-8，保证 `> file.json` 存出来是 UTF-8 文件。

    直接打到 TTY 时保留控制台原编码（Windows 上是 GBK），避免中文乱码；
    但只用 GBK 可编码的字符，所以文本模式里禁止出现 em dash 这类字符。
    """
    try:
        if not sys.stdout.isatty() and (sys.stdout.encoding or "").lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def emit(text: str) -> None:
    """控制台编码保护：Windows GBK 控制台输出超集字符会 UnicodeEncodeError 直接崩。
    JSON 输出也走这里，保证在非 UTF-8 环境下不会半路挂掉。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"))


# 当前正在扫描的源文件行表。scan_python 入口调用 prime_lines 重置。
# _CURRENT 记下这行表属于哪份源码：source_segment 收到不同的 source 时自动重建，
# 而不是静默返回上一个文件的数据。
_LINES: list = []
_LINE_BYTES: list = []
_TOTAL = 0
_CURRENT: str = ""
# 只认 \r / \n 的行尾，且换符留在上一行的末尾。
_LINE_END = re.compile(r"\r\n|\r|\n")


def _split_source_lines(source: str) -> list:
    """按 \\r\\n / \\r / \\n 切行并保留行尾换符，对齐 CPython 的 ast._splitlines_no_ff。

    刻意不用 str.splitlines：它的分隔符集合比解析器宽（\\f \\v U+0085 U+2028
    U+2029 也算），且不带换符，切出来和解析器看到的行对不上。
    """
    if not source:
        return []
    # 按每个换行的结束位置切分：换符归上一行。别改成 re.split 或 (?<=...) 后顾断言——
    # 前者会丢掉换符，后者把换符留到下一行并把 CRLF 切成两刀。
    lines = []
    start = 0
    for m in _LINE_END.finditer(source):
        lines.append(source[start:m.end()])
        start = m.end()
    lines.append(source[start:])
    return lines


def prime_lines(source: str) -> None:
    global _LINES, _LINE_BYTES, _TOTAL, _CURRENT
    _LINES = _split_source_lines(source)
    # col_offset 是字节偏移，所以每行的 UTF-8 字节也必须预先备好
    _LINE_BYTES = [ln.encode() for ln in _LINES]
    _TOTAL = len(_LINES)
    _CURRENT = source


def source_segment(source: str, node: ast.AST) -> str:
    """按 lineno/col_offset 从缓存行表切源码，返回节点原文（保留换行）。

    不用 ast.get_source_segment：它每次调用都重新切行整个文件，7000 个节点就是
    7000 次全文切分，实测单文件要跑数十秒，而 ast.parse 只要 0.1s。按行缓存后
    单次是常数时间。索引口径照抄 CPython：行号先减一，再用 [col:end_col] 截断。
    """
    if source is not _CURRENT:
        prime_lines(source)
    a = node.lineno - 1 if getattr(node, "lineno", None) else -1
    b = node.end_lineno - 1 if getattr(node, "end_lineno", None) else -1
    if a < 0 or b < 0 or a >= _TOTAL or b >= _TOTAL or b < a:
        return ""
    col = node.col_offset or 0
    ecol = node.end_col_offset
    if b == a:
        return _LINE_BYTES[a][col:ecol].decode()
    first = _LINE_BYTES[a][col:].decode()
    last = _LINE_BYTES[b][:ecol].decode()
    return first + "".join(_LINES[a + 1:b]) + last


def header_text(source: str, node: ast.AST) -> str:
    seg = source_segment(source, node)
    return seg.splitlines()[0] if seg else ""


def body_text(source: str, node: ast.AST) -> str:
    """循环体源码，不含 header 行。"""
    seg = source_segment(source, node)
    return "\n".join(seg.splitlines()[1:]) if seg else ""


def is_while_true(node: ast.While) -> bool:
    try:
        return ast.unparse(node.test).strip() in {"True", "1", "1.0"}
    except Exception:
        return False


def swallows(node: ast.ExceptHandler) -> bool:
    """except 体是否只做了「什么都不做」或「跳过这次」。"""
    if not node.body:
        return True
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Continue):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return True
    return False


def _is_dynamic(key: ast.AST) -> bool:
    """键是否为变量/调用结果（而非字面量）→ 可能是 LLM 生成的字符串在驱动分发。"""
    return not isinstance(key, (ast.Constant, ast.List, ast.Tuple))


def dynamic_dispatch(node: ast.AST) -> bool:
    """变量名驱动的分发：若名字来自 LLM 输出，等于跳过注册表校验。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Subscript) and _is_dynamic(func.slice):
        return True
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Subscript):
        if _is_dynamic(func.value.slice):
            return True
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
        if _is_dynamic(node.args[1]):
            return True
    return False


def scan_python(path: Path, source: str, root: Path, result: ScanResult):
    prime_lines(source)
    rel_path = rel(path, root)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        result.errors.append({"path": rel_path, "error": f"SyntaxError: {e.msg} line {e.lineno}"})
        return

    for node in ast.walk(tree):
        # 框架识别 + LLM client 识别（AST 层，覆盖 import x as y）
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in RAW_LLM_MODULES:
                    result.note_client(alias.name.split(".")[0], rel_path, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in RAW_LLM_MODULES:
                result.note_client(top, rel_path, node.lineno)

        # M8: 异常被吞掉
        if isinstance(node, ast.ExceptHandler) and swallows(node):
            result.add(Hint(
                "M8", rel_path, node.lineno, "error_swallowed",
                source_segment(source, node)[:300],
                "异常被吞掉：agent 可能以为自己成功了",
            ))

        # M14: temperature 出现但同一次调用里没有 seed
        if isinstance(node, ast.Call):
            seg = source_segment(source, node)
            if TEMP_KW.search(seg) and not SEED_KW.search(seg):
                result.add(Hint(
                    "M14", rel_path, node.lineno, "temperature_without_seed",
                    seg[:200],
                    "温度非零但未记录 seed，输出不可复现",
                ))

        # M5/M6: 动态分发（未过注册表强校验的常见形态）
        if dynamic_dispatch(node):
            result.add(Hint(
                "M5", rel_path, node.lineno, "dynamic_dispatch",
                source_segment(source, node)[:200],
                "变量名驱动的分发：若名字来自 LLM 输出，等于跳过注册表校验",
            ))

        # M9: 覆盖语义 channel
        if isinstance(node, ast.Subscript):
            seg = source_segment(source, node)
            if "LastValue" in seg and "Annotated" in seg:
                result.add(Hint(
                    "M9", rel_path, node.lineno, "lastvalue_reducer", seg[:200],
                    "覆盖语义 channel：并行分支写同一 key 会抛 INVALID_CONCURRENT_GRAPH_UPDATE；"
                    "单节点写同一 key 会无声覆盖（无 reducer 的默认 channel 就是覆盖语义）",
                ))

    # 循环结构：M1 / L8 / M11
    for node in ast.walk(tree):
        if not isinstance(node, (ast.While, ast.For)):
            continue
        hdr = header_text(source, node)
        body = body_text(source, node)
        combined = hdr + "\n" + body
        # 只有循环预算算 limiter：timeout 是传输层设置，不是循环守卫，
        # 拿它当 has_limiter 会让「有超时」掩盖「没限轮」。
        has_limiter = bool(LOOP_BUDGET_KW.search(combined) or TOKEN_KW.search(combined))
        has_break = bool(re.search(r"\bbreak\b", body))
        bounded_by_range = isinstance(node, ast.For) and bool(
            re.search(r"range\s*\(\s*[A-Za-z_][\w\[\].]?\s*\)", hdr)
        )

        if isinstance(node, ast.While) and is_while_true(node) and not has_limiter:
            note = "无 break 无 limiter：可能无限循环" if not has_break else \
                   "有 break 但无 limiter：终止条件非计数，取决于运行时状态"
            result.add(Hint("M1", rel_path, node.lineno, "loop_without_limiter", hdr, note))
        elif isinstance(node, ast.While) and not has_limiter and not bounded_by_range:
            result.add(Hint("M1", rel_path, node.lineno, "loop_without_limiter", hdr,
                            "while 循环无显式计数上限"))

        # L8: 重试循环无退避 —— 不看有没有 limiter，只看有没有退避。
        # 有 limiter 的即时重试同样会烧钱，只是烧得少一点。
        if RETRY_NAME.search(combined) and not BACKOFF_KW.search(body):
            result.add(Hint("L8", rel_path, node.lineno, "retry_without_backoff", hdr,
                            "重试循环无退避：即时重试相同输入大概率同样失败"))

        # M11: 重试不区分错误类型
        if RETRY_NAME.search(combined) and not ERROR_KIND_KW.search(body):
            result.add(Hint("M11", rel_path, node.lineno, "retry_ignores_error_type", hdr,
                            "重试未区分可重试/不可重试错误"))

    # 行级扫描：limiter 出现位置（M2 判断检查点用）+ import 正则兜底
    for lineno, line in enumerate(source.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in _LINE_LIMITER_KW.finditer(line):
            # 一次 pass 覆盖三层，靠 named group 决定 tier
            g = m.lastgroup
            tier = "transport" if g == "transport" else                    "token_budget" if g == "token" else "loop_budget"
            result.note_limiter(m.group(0), rel_path, lineno, tier)
        for name, regex in FRAMEWORK_IMPORTS.items():
            if regex.match(line):
                result.note_framework(name, rel_path, lineno)
        if RAW_LLM_CLIENTS.match(line):
            for mod in RAW_LLM_MODULES:
                if mod in line:
                    result.note_client(mod, rel_path, lineno)


def scan_js(path: Path, source: str, root: Path, result: ScanResult):
    rel_path = rel(path, root)
    for lineno, line in enumerate(source.splitlines(), 1):
        if line.lstrip().startswith("//"):
            continue
        for hid, pattern, regex in JS_HINTS:
            if regex.search(line):
                result.add(Hint(hid, rel_path, lineno, pattern, line.strip()[:200]))
        for name, regex in FRAMEWORK_IMPORTS.items():
            if regex.match(line):
                result.note_framework(name, rel_path, lineno)
        if RAW_LLM_CLIENTS.match(line):
            for mod in RAW_LLM_MODULES:
                if mod in line:
                    result.note_client(mod, rel_path, lineno)
        for m in _LINE_LIMITER_KW.finditer(line):
            # 一次 pass 覆盖三层，靠 named group 决定 tier
            g = m.lastgroup
            tier = "transport" if g == "transport" else                    "token_budget" if g == "token" else "loop_budget"
            result.note_limiter(m.group(0), rel_path, lineno, tier)


def _hit(names, value: str) -> bool:
    """value 是否以某个 name 开头——按目录名或文件名做前缀匹配。"""
    return any(value.startswith(n) for n in names) if names else False


def iter_files(root: Path, max_kb: int, excludes=(), vendor_dirs=frozenset()):
    """产出 (路径, 后缀)。跳过的文件不产出，但由 main 侧统计原因。

    目录裁剪从根开始（os.walk 原地改 dirnames），所以排除 site-packages
    会拦住它下面所有层级；排除单个文件则需要精确到文件名。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not _is_pruned_dir(d, excludes, vendor_dirs)]
        for fn in filenames:
            p = Path(dirpath) / fn
            suffix = p.suffix.lower()
            if suffix not in PY_SUFFIXES and suffix not in JS_SUFFIXES:
                continue
            if _hit(excludes, fn):
                continue
            try:
                if p.stat().st_size > max_kb * 1024:
                    continue
            except OSError:
                continue
            yield p, suffix


def _is_pruned_dir(name: str, excludes=(), vendor_dirs=frozenset()) -> bool:
    """目录名是否被裁剪。iter_files 与 count_skipped 共用这一条规则，
    保证「扫描到」与「跳过」的口径一致。

    返回包 bool()：`vendor_dirs and name in vendor_dirs` 在 vendor_dirs 是空
    容器时短路返回空容器本身而不是 False——不包的话这个 -> bool 会漏出 () /
    frozenset()，调用方靠「恰好 falsy」才没出错，类型契约和实现脱节。
    要判断「裁剪是不是 vendor 造成的」，把 vendor_dirs 传成空集合即可，
    不需要另设一个函数。
    """
    return bool(name in SKIP_DIRS or name.startswith(".")
                or _hit(excludes, name) or name in vendor_dirs)


def count_skipped(root: Path, max_kb: int, candidates, excludes=(),
               vendor_dirs=frozenset()):
    """统计跳过了哪些文件及原因。

    跳过是常见操作，必须可观测——否则读者会把「扫描到 N 个文件」
    误当成「项目里只有 N 个文件」。这里不裁剪目录地再走一遍，
    才能数出被裁剪目录内部的文件。

    两处刻意不做的事：不对每个文件调 Path.resolve()（一次调用要碰
    一次文件系统，12 万个文件实测要跑一分多钟），也不对每个文件重走
    一遍祖先链。比较用 as_posix() 字符串——两次遍历从同一个已解析的
    root 出发，路径字符串天然一致；「是否在裁剪目录内」改成按目录
    增量继承，os.walk 自顶向下保证父目录先产出。
    """
    scanned = {p.as_posix() for p, _ in candidates}
    counts = {"inside_skipped_dir": 0, "vendor_dir": 0, "excluded": 0,
              "oversize": 0, "unreadable": 0}
    root_s = str(root)
    # 每个目录是否「自身或其祖先被裁剪」。root 本身不算裁剪——我们就是要从它往下走。
    inside_pruned = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        parent = os.path.dirname(dirpath)
        is_root = dirpath == root_s
        nm = os.path.basename(dirpath)
        is_vendor = bool(nm in vendor_dirs)
        own = bool((not is_root) and _is_pruned_dir(nm, excludes, vendor_dirs))
        # os.walk 不跟进符号链接目录，所以这里不需要任何解析就能继承父目录的结论
        pruned = own or inside_pruned.get(parent, False)
        # 只有「裁剪纯粹因为它在 vendor_dirs 里」才算 vendor_dir；
        # 被 SKIP_DIRS / 点前缀 / --exclude 拦住的归 inside_skipped_dir。
        # 判断法：把 vendor_dirs 传成空集合再看一次——两次相减就是 vendor 的贡献。
        # 注意不能写成 `not own`：own 是带 vendor_dirs 算的，vendor 目录自己
        # 就把 own 置成 True，那样 vendor_dir 永远为 False（已踩过）。
        vendor_dir = is_vendor and not is_root and not _is_pruned_dir(nm, excludes)
        inside_pruned[dirpath] = pruned
        for fn in filenames:
            p = Path(dirpath) / fn
            suffix = p.suffix.lower()
            if suffix not in PY_SUFFIXES and suffix not in JS_SUFFIXES:
                continue
            try:
                if p.as_posix() in scanned:
                    continue
            except OSError:
                counts["unreadable"] += 1
                continue
            if pruned:
                why = "vendor_dir" if vendor_dir else "inside_skipped_dir"
            elif _hit(excludes, fn):
                why = "excluded"
            else:
                try:
                    if p.stat().st_size > max_kb * 1024:
                        why = "oversize"
                    else:
                        why = "unreadable"
                except OSError:
                    why = "unreadable"
            counts[why] += 1
    counts = {k: v for k, v in counts.items() if v}
    if counts:
        counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent loop 静态提示扫描（只读，产出候选证据）")
    ap.add_argument("target", help="要扫描的目录或单个源文件")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    ap.add_argument("--max-file-kb", type=int, default=512)
    ap.add_argument("--exclude", action="append", default=[],
                    help="按目录名/文件名前缀排除，可重复（如 --exclude site-packages）")
    ap.add_argument("--scan-vendor", action="store_true",
                    help="同时扫描第三方/研究副本目录（research、examples 等），"
                         "默认跳过——它们不是被审计对象")
    args = ap.parse_args(argv)

    setup_stdout()

    target = Path(args.target).expanduser()
    if not target.exists():
        emit(json.dumps({"error": f"not found: {args.target}"}, ensure_ascii=False))
        return 2

    excludes = tuple(args.exclude or [])
    # 默认跳过第三方目录；显式 --scan-vendor 打开
    vendor_dirs = frozenset() if args.scan_vendor else frozenset(VENDOR_DIRS)
    # 单文件输入时，用它的父目录做根，保证 rel() 输出有意义的相对路径
    root = target if target.is_dir() else target.parent.resolve()
    if target.is_file() and _hit(excludes, target.name):
        emit(json.dumps({"error": f"excluded by --exclude: {target.name}"}, ensure_ascii=False))
        return 2

    if target.is_dir():
        candidates = list(iter_files(
            target.resolve(), args.max_file_kb, excludes, vendor_dirs))
    else:
        candidates = [(target.resolve(), target.suffix.lower())]

    result = ScanResult()
    for p, suffix in candidates:
        result.scanned_files += 1
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result.errors.append({"path": rel(p, root), "error": str(e)})
            continue
        if suffix in PY_SUFFIXES:
            scan_python(p, source, root, result)
        else:
            scan_js(p, source, root, result)

    if target.is_dir():
        result.skipped_reasons = count_skipped(
            target.resolve(), args.max_file_kb, candidates, excludes, vendor_dirs)
        result.skipped_files = result.skipped_reasons.get("total", 0)
        result.skipped_reasons.pop("total", None)

    if args.format == "json":
        d = asdict(result)
        # 检出能力声明：让报告能区分「查了、没有」和「这项脚本查不了」
        d["scanner_coverage"] = {
            "hint_ids": SCANNER_HINT_IDS,
            "context_ids": SCANNER_CONTEXT_IDS,
            "no_signal_ids": SCANNER_NO_SIGNAL_IDS,
            "total_invariants": len(ALL_INVARIANT_IDS),
            "note": "hint_ids 能产出 pattern 提示；context_ids 只提供线索不构成判定；"
                    "no_signal_ids 必须靠逐行阅读，无提示不等于该项通过",
        }
        # 划分坏了说明覆盖声明本身不可信，比静默交出一份加总对不上的数据更诚实。
        # path 留空表示这是脚本自身的问题，不是被审计项目的某一行。
        _h = set(SCANNER_HINT_IDS); _c = set(SCANNER_CONTEXT_IDS)
        got = len(SCANNER_HINT_IDS) + len(SCANNER_CONTEXT_IDS) + len(SCANNER_NO_SIGNAL_IDS)
        if _h & _c or got != len(ALL_INVARIANT_IDS):
            result.errors.append({
                "path": "",
                "error": "scanner_coverage 划分不自洽%s，与骨架合计 %d 应为 %d；"
                         "扫描结果照常产出，但 scanner_coverage 字段不可信，覆盖率不要引用"
                         % ("" if not (_h & _c) else "（hint_ids 与 context_ids 重叠 %s）"
                            % sorted(_h & _c), got, len(ALL_INVARIANT_IDS))})
        d.pop("_fw_seen", None)
        d.pop("_cl_seen", None)
        emit(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        fw = {k: len(v) for k, v in result.framework_imports.items()}
        cl = {k: len(v) for k, v in result.raw_llm_clients.items()}
        emit(f"scanned_files={result.scanned_files} skipped={result.skipped_files} errors={len(result.errors)}")
        if result.skipped_reasons:
            emit(f"skipped_reasons={result.skipped_reasons}")
        emit(f"framework_imports={fw}")
        emit(f"raw_llm_clients={cl}")
        emit(f"limiter_mentions={len(result.limiter_mentions)}")
        for h in result.hints:
            emit(f"  [{h.id}] {h.path}:{h.line} {h.pattern} | {h.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
