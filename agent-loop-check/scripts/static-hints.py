#!/usr/bin/env python3
"""Agent loop 静态提示扫描器 — 只产生候选证据，不做判定。

用法:
    python static-hints.py <目标目录> [--format json|text] [--max-file-kb 512]

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
}
PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

# 预算/limiter 关键词。M1 的核心：出现即「这里可能有限制器」，缺失才值得看。
LIMITER_KW = re.compile(
    r"""max_turns|max_iterations|max_iter|max_steps|max_retries|max_retry
        |max_loop|loop_limit|recursion_limit|max_executions
        |max_seconds|max_duration|timeout|deadline
        |budget|cost_limit|max_cost
    """,
    re.VERBOSE | re.IGNORECASE,
)
TOKEN_KW = re.compile(r"max_tokens|max_output_tokens|max_completion_tokens", re.IGNORECASE)

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

    def note_limiter(self, text: str, path: str, line: int):
        self.limiter_mentions.append({"text": text, "path": path, "line": line})

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


def source_segment(source: str, node: ast.AST) -> str:
    try:
        seg = ast.get_source_segment(source, node)
        return " ".join(seg.split()) if seg else ""
    except Exception:
        return ""


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
        has_limiter = bool(LIMITER_KW.search(combined))
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
        for m in LIMITER_KW.finditer(line):
            result.note_limiter(m.group(0), rel_path, lineno)
        for m in TOKEN_KW.finditer(line):
            result.note_limiter(m.group(0), rel_path, lineno)
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
        for m in LIMITER_KW.finditer(line):
            result.note_limiter(m.group(0), rel_path, lineno)


def iter_files(root: Path, max_kb: int):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            suffix = p.suffix.lower()
            if suffix not in PY_SUFFIXES and suffix not in JS_SUFFIXES:
                continue
            try:
                if p.stat().st_size > max_kb * 1024:
                    continue
            except OSError:
                continue
            yield p, suffix


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent loop 静态提示扫描（只读，产出候选证据）")
    ap.add_argument("target", help="要扫描的目录")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    ap.add_argument("--max-file-kb", type=int, default=512)
    args = ap.parse_args(argv)

    setup_stdout()

    root = Path(args.target).resolve()
    if not root.is_dir():
        emit(json.dumps({"error": f"not a directory: {args.target}"}, ensure_ascii=False))
        return 2

    result = ScanResult()
    for p, suffix in iter_files(root, args.max_file_kb):
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

    if args.format == "json":
        d = asdict(result)
        d.pop("_fw_seen", None)
        d.pop("_cl_seen", None)
        emit(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        fw = {k: len(v) for k, v in result.framework_imports.items()}
        cl = {k: len(v) for k, v in result.raw_llm_clients.items()}
        emit(f"scanned_files={result.scanned_files} errors={len(result.errors)}")
        emit(f"framework_imports={fw}")
        emit(f"raw_llm_clients={cl}")
        emit(f"limiter_mentions={len(result.limiter_mentions)}")
        for h in result.hints:
            emit(f"  [{h.id}] {h.path}:{h.line} {h.pattern} | {h.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
