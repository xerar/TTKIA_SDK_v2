"""
efest(OS) — Agentic coding assistant powered by TTKIA.

Runs as an interactive CLI loop:
1. User describes a task
2. TTKIA backend (Claude) responds with structured actions
3. CLI executes actions locally (read/write files, search project, run scripts)
4. Results feed back into the conversation for next iteration
"""

from __future__ import annotations

import os
import re
import sys
import subprocess
import tempfile
import ast as _ast
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree
from rich.table import Table
from rich.theme import Theme
from rich.syntax import Syntax
from rich import box

from ttkia_sdk import TTKIAClient


# ═══════════════════════════════════════════════════════════
# RICH THEME + HEX CONSTANTS
# ═══════════════════════════════════════════════════════════
#
# Theme names work ONLY inside [markup] tags:  [ttkia.teal]text[/]
# For style= parameters, use the _HEX constants below.

_THEME = Theme({
    "ttkia.teal":       "#67C3C8",
    "ttkia.navy":       "#141D32",
    "ttkia.blue":       "#0066FF",
    "ttkia.grey":       "#8F97AF",
    "ttkia.pale":       "#B0B6CA",
    "ttkia.amber":      "#CDA644",
    "ttkia.coral":      "#C96C64",
    "ttkia.green":      "#528889",
    "ttkia.white":      "#F2F4FF",
    "ttkia.success":    "bold #528889",
    "ttkia.error":      "bold #C96C64",
    "ttkia.warning":    "bold #CDA644",
    "ttkia.info":       "dim #67C3C8",
    "ttkia.dim":        "dim #8F97AF",
})

_TEAL   = "#67C3C8"
_PALE   = "#B0B6CA"
_NAVY   = "#141D32"
_GREY   = "#8F97AF"
_AMBER  = "#CDA644"
_GREEN  = "#528889"
_CORAL  = "#C96C64"
_WHITE  = "#F2F4FF"

console = Console(theme=_THEME)


# ═══════════════════════════════════════════════════════════
# ACTION MODELS
# ═══════════════════════════════════════════════════════════

@dataclass
class Action:
    type: str
    path: Optional[str] = None
    content: Optional[str] = None
    search_text: Optional[str] = None
    replace_text: Optional[str] = None
    pattern: Optional[str] = None
    line_range: Optional[str] = None


@dataclass
class ActionResult:
    action: Action
    success: bool
    output: str
    skipped: bool = False


# ═══════════════════════════════════════════════════════════
# SCRIPT SAFETY VALIDATOR
# ═══════════════════════════════════════════════════════════

_BLOCKED_PATTERNS = [
    r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
    r'\bos\.exec', r'\bos\.remove\b', r'\bos\.unlink\b',
    r'\bshutil\.rmtree\b', r'\bshutil\.move\b',
    r'\b__import__\b', r'\beval\s*\(', r'\bexec\s*\(',
    r'\brequests\b', r'\bhttpx\b', r'\burllib\b',
    r'\bsocket\b', r'\bsmtplib\b', r'\bftplib\b',
]

_ALLOWED_IMPORTS = {
    'openpyxl', 'pptx', 'csv', 'json', 'datetime', 'os', 'os.path',
    'pathlib', 'math', 'collections', 'itertools', 're',
    'matplotlib', 'PIL', 'io', 'textwrap', 'decimal',
}


def _check_script_safety(script: str) -> Optional[str]:
    for pattern in _BLOCKED_PATTERNS:
        m = re.search(pattern, script)
        if m:
            return f"Blocked pattern: '{m.group()}'"
    for m in re.finditer(r'^\s*(?:from\s+([\w.]+)|import\s+([\w.]+))', script, re.MULTILINE):
        module = (m.group(1) or m.group(2)).split('.')[0]
        if module not in _ALLOWED_IMPORTS:
            return f"Blocked import: '{module}' (not in allowed list)"
    return None


# ═══════════════════════════════════════════════════════════
# ACTION PARSER
# ═══════════════════════════════════════════════════════════

_RE_SELF_CLOSE = re.compile(r'<action\s+([^>]*?)\s*/\s*>', re.DOTALL)
_RE_BLOCK = re.compile(r'<action\s+([^>]*?)>(?P<body>.*?)</action[^>]*>', re.DOTALL)
_RE_ATTR = re.compile(r'(\w+)="([^"]*)"')
_RE_SEARCH = re.compile(r'<search>(.*?)</search>', re.DOTALL)
_RE_REPLACE = re.compile(r'<replace>(.*?)</replace>', re.DOTALL)


def _parse_attrs(attr_str: str) -> dict:
    return dict(_RE_ATTR.findall(attr_str))


def parse_actions(text: str) -> Tuple[str, List[Action]]:
    actions: List[Action] = []
    for m in _RE_SELF_CLOSE.finditer(text):
        attrs = _parse_attrs(m.group(1))
        actions.append(Action(
            type=attrs.get("type", ""), path=attrs.get("path"),
            pattern=attrs.get("pattern"), line_range=attrs.get("lines"),
        ))
    for m in _RE_BLOCK.finditer(text):
        attrs = _parse_attrs(m.group(1))
        body = m.group("body").strip()
        a = Action(
            type=attrs.get("type", ""), path=attrs.get("path"),
            pattern=attrs.get("pattern"), line_range=attrs.get("lines"),
        )
        if a.type == "edit_file":
            sm = _RE_SEARCH.search(body)
            rm = _RE_REPLACE.search(body)
            a.search_text = sm.group(1) if sm else None
            a.replace_text = rm.group(1) if rm else None
        elif a.type == "write_file":
            a.content = body
        elif a.type == "run_script":
            a.content = body
            a.path = attrs.get("output") or attrs.get("path")
        actions.append(a)

    prose = _RE_BLOCK.sub("", text)
    prose = _RE_SELF_CLOSE.sub("", prose)
    prose = re.sub(r'\n{3,}', '\n\n', prose).strip()
    return prose, actions


# ═══════════════════════════════════════════════════════════
# LOCAL EXECUTOR
# ═══════════════════════════════════════════════════════════

_MAX_READ_SIZE = 100_000
_MAX_SEARCH_RESULTS = 50


def _resolve_path(path: str, root: Path) -> Path:
    resolved = (root / path).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise ValueError(f"Path traversal blocked: {path}")
    return resolved


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def execute_action(action: Action, root: Path) -> ActionResult:
    try:
        handlers = {
            "read_file": _exec_read, "write_file": _exec_write,
            "edit_file": _exec_edit, "search": _exec_search,
            "run_script": _exec_run_script,
        }
        handler = handlers.get(action.type)
        if not handler:
            return ActionResult(action=action, success=False, output=f"Unknown: {action.type}")
        return handler(action, root)
    except Exception as e:
        return ActionResult(action=action, success=False, output=f"Error: {e}")


def _exec_read(action: Action, root: Path) -> ActionResult:
    fp = _resolve_path(action.path, root)
    if not fp.exists():
        return ActionResult(action=action, success=False, output=f"Not found: {action.path}")
    if fp.stat().st_size > _MAX_READ_SIZE:
        return ActionResult(action=action, success=False,
                            output=f"Too large ({fp.stat().st_size:,} bytes)")
    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ActionResult(action=action, success=False, output=f"Binary file: {action.path}")

    all_lines = content.split('\n')
    total = len(all_lines)
    line_range = getattr(action, 'line_range', None)
    if line_range:
        try:
            parts = line_range.split('-')
            start = max(1, int(parts[0])) - 1
            end = min(total, int(parts[1])) if len(parts) > 1 else total
            selected = all_lines[start:end]
            numbered = '\n'.join(f"{i+start+1:4d} │ {l}" for i, l in enumerate(selected))
            return ActionResult(action=action, success=True,
                                output=f"[{action.path}] (lines {start+1}-{end} of {total})\n{numbered}")
        except (ValueError, IndexError):
            pass
    numbered = '\n'.join(f"{i+1:4d} │ {l}" for i, l in enumerate(all_lines))
    return ActionResult(action=action, success=True,
                        output=f"[{action.path}] ({total} lines)\n{numbered}")


def _exec_write(action: Action, root: Path) -> ActionResult:
    fp = _resolve_path(action.path, root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    existed = fp.exists()
    fp.write_text(action.content or "", encoding="utf-8")
    verb = "Updated" if existed else "Created"
    lines = (action.content or "").count('\n') + 1
    return ActionResult(action=action, success=True, output=f"{verb} {action.path} ({lines} lines)")


def _exec_edit(action: Action, root: Path) -> ActionResult:
    fp = _resolve_path(action.path, root)
    if not fp.exists():
        return ActionResult(action=action, success=False, output=f"Not found: {action.path}")
    if not action.search_text:
        return ActionResult(action=action, success=False, output="Requires <search> block")
    content = fp.read_text(encoding="utf-8")
    count = content.count(action.search_text)
    if count == 0:
        return ActionResult(action=action, success=False,
                            output=f"Text not found in {action.path}. Read the file first.")
    if count > 1:
        return ActionResult(action=action, success=False,
                            output=f"Found {count} matches — make search text unique.")
    new_content = content.replace(action.search_text, action.replace_text or "", 1)
    fp.write_text(new_content, encoding="utf-8")
    return ActionResult(action=action, success=True,
                        output=f"Edited {action.path} ({len(action.search_text)} → {len(action.replace_text or '')} chars)")


def _exec_search(action: Action, root: Path) -> ActionResult:
    search_path = action.path or "."
    target = _resolve_path(search_path, root)
    if not target.exists():
        return ActionResult(action=action, success=False, output=f"Not found: {search_path}")
    try:
        result = subprocess.run(
            ["grep", "-rn",
             "--include=*.py", "--include=*.js", "--include=*.yaml",
             "--include=*.yml", "--include=*.json", "--include=*.md",
             "--include=*.html", "--include=*.css", "--include=*.sh",
             "--include=*.toml", "--include=*.cfg", "--include=*.txt",
             "-E", action.pattern or "", str(target)],
            capture_output=True, text=True, timeout=10, cwd=str(root),
        )
    except subprocess.TimeoutExpired:
        return ActionResult(action=action, success=False, output="Timed out (10s)")
    lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
    if not lines:
        return ActionResult(action=action, success=True, output=f"No matches: {action.pattern}")
    total = len(lines)
    if total > _MAX_SEARCH_RESULTS:
        lines = lines[:_MAX_SEARCH_RESULTS]
    root_str = str(root) + "/"
    output = '\n'.join(l.replace(root_str, "") for l in lines)
    if total > _MAX_SEARCH_RESULTS:
        output += f"\n... ({total - _MAX_SEARCH_RESULTS} more)"
    return ActionResult(action=action, success=True, output=f"Found {total} matches:\n{output}")


def _exec_run_script(action: Action, root: Path) -> ActionResult:
    if not action.content:
        return ActionResult(action=action, success=False, output="Requires script content")
    warning = _check_script_safety(action.content)
    if warning:
        return ActionResult(action=action, success=False, output=warning)
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', dir=str(root), delete=False, encoding='utf-8'
    ) as f:
        f.write(action.content)
        script_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=60, cwd=str(root),
        )
        parts = []
        if result.stdout.strip():
            parts.append(result.stdout.strip())
        if result.returncode != 0:
            parts.append(f"stderr: {result.stderr.strip()}")
            return ActionResult(action=action, success=False,
                                output='\n'.join(parts) or f"Exit code {result.returncode}")
        if action.path:
            out_file = _resolve_path(action.path, root)
            if out_file.exists():
                size = out_file.stat().st_size
                parts.append(f"Generated: {action.path} ({size:,} bytes)")
        return ActionResult(action=action, success=True,
                            output='\n'.join(parts) or "Script executed OK")
    except subprocess.TimeoutExpired:
        return ActionResult(action=action, success=False, output="Script timed out (60s)")
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# PROJECT TREE (rich.tree)
# ═══════════════════════════════════════════════════════════

_SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox',
    '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build',
    '.eggs', '*.egg-info', '.idea', '.vscode',
}
_SKIP_FILES = {'.DS_Store', 'Thumbs.db', '.gitkeep'}
_FILE_ICONS = {
    '.py': '🐍', '.js': '📜', '.ts': '📘', '.yaml': '⚙️', '.yml': '⚙️',
    '.json': '📋', '.md': '📝', '.html': '🌐', '.css': '🎨',
    '.sh': '🔧', '.toml': '⚙️', '.sql': '🗃️', '.dockerfile': '🐳',
    '.xlsx': '📊', '.pptx': '📰', '.pdf': '📕',
}


def build_rich_tree(root: Path, max_depth: int = 3) -> Tree:
    tree = Tree(f"[bold ttkia.teal]{root.name}/[/]", guide_style=_PALE)
    _add_tree_nodes(root, tree, 0, max_depth)
    return tree


def _add_tree_nodes(path: Path, tree: Tree, depth: int, max_depth: int):
    if depth >= max_depth:
        return
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return
    entries = [
        e for e in entries
        if e.name not in _SKIP_FILES
        and not any(e.name == sd or (sd.startswith('*') and e.name.endswith(sd[1:]))
                    for sd in _SKIP_DIRS)
        and not e.name.startswith('.')
    ]
    for entry in entries:
        if entry.is_dir():
            branch = tree.add(f"[bold ttkia.teal]{entry.name}/[/]")
            _add_tree_nodes(entry, branch, depth + 1, max_depth)
        else:
            icon = _FILE_ICONS.get(entry.suffix.lower(), "📄")
            size = entry.stat().st_size
            size_str = f"{size:,}B" if size < 1024 else f"{size/1024:.0f}K"
            tree.add(f"{icon} [ttkia.white]{entry.name}[/]  [ttkia.dim]{size_str}[/]")


# ═══════════════════════════════════════════════════════════
# REPO MAP BUILDER
# ═══════════════════════════════════════════════════════════

_REPO_MAP_EXTENSIONS = {'.py', '.js', '.ts', '.yaml', '.yml', '.json', '.toml', '.cfg', '.sh', '.md'}
_REPO_MAP_MAX_FILE_SIZE = 200_000


def _extract_python_signatures(filepath: Path) -> List[str]:
    try:
        source = filepath.read_text(encoding='utf-8')
        tree = _ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    sigs = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef):
            bases = ', '.join(getattr(b, 'id', getattr(b, 'attr', '?')) for b in node.bases)
            bases_str = f"({bases})" if bases else ""
            sigs.append(f"  class {node.name}{bases_str}  [line {node.lineno}]")
            for item in node.body:
                if isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    args = ', '.join(a.arg for a in item.args.args)
                    prefix = "async " if isinstance(item, _ast.AsyncFunctionDef) else ""
                    sigs.append(f"    {prefix}def {item.name}({args})  [line {item.lineno}]")
        elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            if hasattr(node, 'col_offset') and node.col_offset == 0:
                args = ', '.join(a.arg for a in node.args.args)
                prefix = "async " if isinstance(node, _ast.AsyncFunctionDef) else ""
                sigs.append(f"  {prefix}def {node.name}({args})  [line {node.lineno}]")
    return sigs


def _extract_yaml_sections(filepath: Path) -> List[str]:
    try:
        content = filepath.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return []
    sections = []
    for line in content.split('\n'):
        stripped = line.rstrip()
        if stripped and not stripped.startswith(' ') and not stripped.startswith('#') and ':' in stripped:
            key = stripped.split(':')[0].strip()
            if key and len(key) < 60:
                sections.append(f"  {key}:")
    return sections[:20]


def _extract_markdown_headers(filepath: Path) -> List[str]:
    try:
        content = filepath.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return []
    headers = []
    for line in content.split('\n'):
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            text = line.lstrip('#').strip()
            if text:
                headers.append(f"  {'  ' * (level - 1)}{text}")
    return headers[:15]


def _extract_shell_functions(filepath: Path) -> List[str]:
    try:
        content = filepath.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return []
    funcs = []
    for m in re.finditer(r'^(\w+)\s*\(\)', content, re.MULTILINE):
        funcs.append(f"  {m.group(1)}()")
    return funcs[:20]


def build_repo_map(root: Path, max_depth: int = 3) -> str:
    lines = [f"# Project: {root.name}"]
    _build_repo_map_recursive(root, root, 0, max_depth, lines)
    return '\n'.join(lines)


def _build_repo_map_recursive(path: Path, root: Path, depth: int, max_depth: int, lines: list):
    if depth >= max_depth:
        return
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return
    entries = [
        e for e in entries
        if e.name not in _SKIP_FILES
        and not any(e.name == sd or (sd.startswith('*') and e.name.endswith(sd[1:]))
                    for sd in _SKIP_DIRS)
        and not e.name.startswith('.')
    ]
    for entry in entries:
        rel = entry.relative_to(root)
        if entry.is_dir():
            lines.append(f"\n## {rel}/")
            _build_repo_map_recursive(entry, root, depth + 1, max_depth, lines)
        elif entry.suffix.lower() in _REPO_MAP_EXTENSIONS:
            try:
                size = entry.stat().st_size
                if size > _REPO_MAP_MAX_FILE_SIZE:
                    line_count = "large file"
                else:
                    content = entry.read_text(encoding='utf-8', errors='ignore')
                    line_count = f"{content.count(chr(10)) + 1} lines"
            except OSError:
                line_count = "?"
            lines.append(f"\n{rel} ({line_count})")
            if entry.suffix == '.py' and size <= _REPO_MAP_MAX_FILE_SIZE:
                lines.extend(_extract_python_signatures(entry))
            elif entry.suffix in ('.yaml', '.yml') and size <= _REPO_MAP_MAX_FILE_SIZE:
                lines.extend(_extract_yaml_sections(entry))
            elif entry.suffix == '.md' and size <= _REPO_MAP_MAX_FILE_SIZE:
                lines.extend(_extract_markdown_headers(entry))
            elif entry.suffix == '.sh' and size <= _REPO_MAP_MAX_FILE_SIZE:
                lines.extend(_extract_shell_functions(entry))


# ═══════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════

_MAX_ITERATIONS = 20

_ACTION_STYLE = {
    "read_file":  {"icon": "📖", "label": "READ",   "markup": "ttkia.teal",  "confirm": False},
    "write_file": {"icon": "✏️",  "label": "WRITE",  "markup": "ttkia.amber", "confirm": True},
    "edit_file":  {"icon": "🔧", "label": "EDIT",   "markup": "ttkia.amber", "confirm": True},
    "search":     {"icon": "🔍", "label": "SEARCH", "markup": "ttkia.teal",  "confirm": False},
    "run_script": {"icon": "🐍", "label": "SCRIPT", "markup": "ttkia.amber", "confirm": True},
}


class CodeAgent:
    """Interactive coding agent — TTKIA backend + local execution + rich UI."""

    def __init__(self, client: TTKIAClient, root: Path, style: str = "detailed"):
        self.client = client
        self.root = root.resolve()
        self.prompt = "code_agent"
        self.style = style
        self.conversation_id: Optional[str] = None
        self._iteration = 0
        self._total_tokens = 0
        self._total_actions = 0
        self._attached: Dict[str, str] = {}
        self._attached_tokens: int = 0
        self._token_budget: int = 30000
        self._repo_map: str = ""
        self._history: List[dict] = []
        self._last_response: str = ""

    # ──────────────────────────────────────────────────────
    # CONTEXT BUILDING
    # ──────────────────────────────────────────────────────

    def _build_context_prefix(self) -> str:
        if not self._repo_map:
            self._repo_map = build_repo_map(self.root, max_depth=3)
        parts = [
            "<project_context>",
            f"Working directory: {self.root}",
            "<repo_map>",
            self._repo_map,
            "</repo_map>",
        ]
        if self._attached:
            parts.append("<attached_files>")
            for path, content in self._attached.items():
                lines = content.split('\n')
                numbered = '\n'.join(f"{i+1:4d} | {l}" for i, l in enumerate(lines))
                parts.append(f'<file path="{path}" lines="{len(lines)}">')
                parts.append(numbered)
                parts.append("</file>")
            parts.append("</attached_files>")
        parts.append("</project_context>\n")
        return '\n'.join(parts)

    def _build_history_block(self) -> str:
        if len(self._history) <= 2:
            return ""
        lines = ["<conversation_history>"]
        for entry in self._history[:-2]:
            if entry["role"] == "assistant":
                actions_str = ", ".join(entry.get("actions", []))
                lines.append("  <step role='assistant'>")
                if entry.get("summary"):
                    lines.append(f"    <summary>{entry['summary']}</summary>")
                if actions_str:
                    lines.append(f"    <actions>{actions_str}</actions>")
                lines.append("  </step>")
            elif entry["role"] == "system":
                lines.append("  <step role='execution'>Results received</step>")
        lines.append("</conversation_history>\n")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────
    # ACTION DISPLAY & EXECUTION
    # ──────────────────────────────────────────────────────

    def _show_diff_preview(self, action: Action):
        if action.type == "edit_file" and action.search_text and action.replace_text:
            console.print()
            console.print(Syntax(action.search_text.strip(), "python", theme="monokai",
                                 line_numbers=False, background_color="#1a1a2e", padding=(0, 1)))
            console.print(Text("  ↓ replaced by ↓", style=f"dim {_GREY}"))
            console.print(Syntax(action.replace_text.strip(), "python", theme="monokai",
                                 line_numbers=False, background_color="#1a2e1a", padding=(0, 1)))
        elif action.type == "write_file" and action.content:
            preview = '\n'.join(action.content.split('\n')[:12])
            total = action.content.count('\n') + 1
            console.print()
            console.print(Syntax(preview, "python", theme="monokai",
                                 line_numbers=False, background_color="#1a2e1a", padding=(0, 1)))
            if total > 12:
                console.print(f"  [ttkia.dim]... +{total - 12} more lines ({len(action.content):,} chars)[/]")
        elif action.type == "run_script" and action.content:
            preview = '\n'.join(action.content.strip().split('\n')[:12])
            total = action.content.strip().count('\n') + 1
            console.print()
            console.print(Syntax(preview, "python", theme="monokai",
                                 line_numbers=True, padding=(0, 1)))
            if total > 12:
                console.print(f"  [ttkia.dim]... +{total - 12} more lines[/]")

    def _confirm_action(self, action: Action) -> bool:
        self._show_diff_preview(action)
        try:
            resp = console.input(
                "  [ttkia.amber bold]Apply?[/] [ttkia.dim]\\[[ttkia.green]y[/ttkia.green]/[ttkia.coral]n[/ttkia.coral]][/] "
            ).strip().lower()
            return resp in ("y", "yes", "a", "all", "")
        except (KeyboardInterrupt, EOFError):
            return False

    def _execute_actions(self, actions: List[Action]) -> str:
        results = []
        if actions:
            console.print()
            console.rule(f"[ttkia.teal]⚡ Actions ({len(actions)})[/]", style=_TEAL)

        for action in actions:
            cfg = _ACTION_STYLE.get(action.type, {"icon": "⚡", "label": "???", "markup": "ttkia.grey", "confirm": True})
            target = action.path or action.pattern or ""
            mk = cfg["markup"]
            console.print(f"\n  {cfg['icon']} [{mk} bold]{cfg['label']}[/]  [ttkia.white]{target}[/]")

            if cfg.get("confirm"):
                if not self._confirm_action(action):
                    results.append(ActionResult(action=action, success=False,
                                                output="Skipped by user", skipped=True))
                    console.print("  [ttkia.warning]⚠ Skipped[/]")
                    continue

            result = execute_action(action, self.root)
            results.append(result)
            self._total_actions += 1

            if result.success:
                if action.type == "read_file":
                    console.print(f"  [ttkia.success]✔ Read {result.output.count(chr(10))} lines[/]")
                elif action.type == "search":
                    console.print(f"  [ttkia.success]✔ {result.output.split(chr(10))[0]}[/]")
                else:
                    console.print(f"  [ttkia.success]✔ {result.output}[/]")
            else:
                console.print(f"  [ttkia.error]✖ {result.output}[/]")

        parts = []
        for r in results:
            status = "SUCCESS" if r.success else ("SKIPPED" if r.skipped else "ERROR")
            parts.append(
                f"<action_result type=\"{r.action.type}\" "
                f"path=\"{r.action.path or ''}\" "
                f"status=\"{status}\">\n{r.output}\n</action_result>"
            )
        return '\n\n'.join(parts)

    # ──────────────────────────────────────────────────────
    # MAIN ASK LOOP (streaming without flicker)
    # ──────────────────────────────────────────────────────

    def ask(self, user_query: str) -> str:
        self._iteration = 0

        if self.conversation_id is None:
            query = self._build_context_prefix() + f"<user_request>\n{user_query}\n</user_request>"
        else:
            query = user_query

        final_prose = ""
        while self._iteration < _MAX_ITERATIONS:
            self._iteration += 1

            full_text = ""
            token_counts = {}
            streamed_text = False

            try:
                for event in self.client.code_query_stream(
                    query,
                    conversation_id=self.conversation_id,
                    title="[TTKIA Code]" if not self.conversation_id else None,
                ):
                    etype = event.get("type", "")

                    if etype == "mcp":
                        console.print(f"  [ttkia.info]● {event.get('content', '')}[/]")

                    elif etype == "text":
                        if not streamed_text:
                            # Print header before first chunk
                            console.rule(
                                f"[ttkia.teal]💬 Response[/]"
                                f"  [ttkia.dim](step {self._iteration})[/]",
                                style=_TEAL,
                            )
                            streamed_text = True

                        chunk = event.get("content", "")
                        full_text += chunk
                        # Raw streaming: just print chunks as they arrive
                        sys.stdout.write(chunk)
                        sys.stdout.flush()

                    elif etype == "done":
                        if streamed_text:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        if not self.conversation_id:
                            self.conversation_id = event.get("conversation_id")
                        token_counts = event.get("token_counts", {})
                        self._total_tokens += token_counts.get("input", 0) + token_counts.get("output", 0)

                    elif etype == "error":
                        console.print(f"  [ttkia.error]✖ {event.get('content', 'Unknown error')}[/]")
                        return ""

            except Exception as e:
                console.print(f"  [ttkia.error]✖ {e}[/]")
                return ""

            # Parse actions from complete response
            prose, actions = parse_actions(full_text)

            # Render final markdown in a panel (replaces the raw stream)
            if prose and streamed_text:
                # Clear raw streamed text and re-render as formatted markdown
                console.print()
                console.print(Panel(
                    Markdown(prose, code_theme="monokai"),
                    border_style=_TEAL, padding=(1, 2),
                    title="[ttkia.teal]💬 Response[/]", title_align="left",
                    subtitle=f"[ttkia.dim]step {self._iteration}[/]", subtitle_align="right",
                ))

            final_prose = prose
            self._last_response = prose

            # History
            self._history.append({
                "role": "assistant",
                "summary": prose[:300] if prose else "",
                "actions": [f"{a.type}:{a.path or a.pattern or ''}" for a in actions],
            })

            if not actions:
                return prose

            feedback = self._execute_actions(actions)

            self._history.append({
                "role": "system",
                "results": feedback[:500],
            })

            history_block = self._build_history_block()
            query = (
                f"<original_request>{user_query}</original_request>\n\n"
                f"{history_block}"
                f"<action_results>\n{feedback}\n</action_results>\n\n"
                f"The original user request is shown above. "
                f"Continue with ONLY what was requested. Do NOT add features or changes that were not asked for. "
                f"If the task is complete, summarize what was found or done."
            )

        console.print("[ttkia.error]✖ Reached max iterations[/]")
        return final_prose

    # ──────────────────────────────────────────────────────
    # ATTACH / DETACH
    # ──────────────────────────────────────────────────────

    def _attach_file(self, rel_path: str) -> bool:
        try:
            fp = _resolve_path(rel_path, self.root)
            if not fp.exists():
                console.print(f"  [ttkia.error]✖ File not found: {rel_path}[/]")
                return False
            if not fp.is_file():
                console.print(f"  [ttkia.error]✖ Not a file: {rel_path}[/]")
                return False
            if fp.stat().st_size > _MAX_READ_SIZE:
                console.print(f"  [ttkia.error]✖ Too large: {fp.stat().st_size:,} bytes[/]")
                return False
            try:
                content = fp.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                console.print(f"  [ttkia.error]✖ Binary file: {rel_path}[/]")
                return False

            tokens = _estimate_tokens(content)
            new_total = self._attached_tokens - _estimate_tokens(self._attached.get(rel_path, "")) + tokens
            if new_total > self._token_budget:
                console.print(f"  [ttkia.error]✖ Token budget exceeded: {new_total:,} / {self._token_budget:,}[/]")
                return False

            self._attached[rel_path] = content
            self._attached_tokens = sum(_estimate_tokens(c) for c in self._attached.values())
            lines = content.count('\n') + 1
            console.print(f"  [ttkia.success]✔ Attached {rel_path} ({lines} lines, ~{tokens:,} tokens)[/]")
            return True
        except ValueError as e:
            console.print(f"  [ttkia.error]✖ {e}[/]")
            return False

    def _detach_file(self, rel_path: str) -> bool:
        if rel_path not in self._attached:
            console.print(f"  [ttkia.error]✖ Not attached: {rel_path}[/]")
            return False
        del self._attached[rel_path]
        self._attached_tokens = sum(_estimate_tokens(c) for c in self._attached.values())
        console.print(f"  [ttkia.success]✔ Detached {rel_path}[/]")
        return True

    def _show_attached(self):
        if not self._attached:
            console.print("  [ttkia.info]● No files attached[/]")
            return
        table = Table(box=box.ROUNDED, border_style=_TEAL, padding=(0, 1))
        table.add_column("📎 File", style=_WHITE)
        table.add_column("Lines", justify="right", style=_GREY)
        table.add_column("Tokens", justify="right", style=_GREY)
        for path, content in self._attached.items():
            table.add_row(path, str(content.count('\n') + 1), f"~{_estimate_tokens(content):,}")
        table.add_section()
        table.add_row(
            "[ttkia.grey]Total[/]", "",
            f"[ttkia.teal]{self._attached_tokens:,}[/] / {self._token_budget:,}"
        )
        console.print(table)

    def _save_response(self, filename: Optional[str] = None):
        if not self._last_response:
            console.print("  [ttkia.warning]⚠ No response to save yet[/]")
            return
        if not filename:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"efestos_{ts}.md"
        try:
            fp = self.root / filename
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(self._last_response, encoding='utf-8')
            console.print(f"  [ttkia.success]✔ Saved to {filename} ({len(self._last_response):,} chars)[/]")
        except Exception as e:
            console.print(f"  [ttkia.error]✖ Save failed: {e}[/]")

    # ──────────────────────────────────────────────────────
    # UI: BANNER, STATS, GOODBYE, HELP
    # ──────────────────────────────────────────────────────

    def _show_banner(self):
        console.print()
        console.rule(style=_TEAL)
        console.print()
        logo = Text(
            "          ██████╗ ███████╗███████╗███████╗████████╗ ██████╗ ███████╗\n"
            "         ██╔════╝ ██╔════╝██╔════╝██╔════╝╚══██╔══╝██╔═══██╗██╔════╝\n"
            "         █████╗   █████╗  █████╗  ███████╗   ██║   ██║   ██║███████╗\n"
            "         ██╔══╝   ██╔══╝  ██╔══╝  ╚════██║   ██║   ██║   ██║╚════██║\n"
            "         ███████╗ ██║     ███████╗███████║   ██║   ╚██████╔╝███████║\n"
            "         ╚══════╝ ╚═╝     ╚══════╝╚══════╝   ╚═╝    ╚═════╝ ╚══════╝"
        )
        console.print(logo, style=f"bold {_TEAL}", justify="center")
        console.print()
        console.print(
            f"[bold {_WHITE}]powered by TTKIA[/]    [dim {_GREY}]v1.0[/]",
            justify="center",
        )
        console.print()
        console.rule(style=_TEAL)
        console.print()

        # Session panel
        session_table = Table(show_header=False, box=None, padding=(0, 1))
        session_table.add_column("key", style=_GREY, width=12)
        session_table.add_column("value")
        session_table.add_row("Project", f"[bold ttkia.white]{self.root.name}/[/]")
        session_table.add_row("Path", f"[ttkia.dim]{self.root}[/]")
        session_table.add_row("Backend", f"[ttkia.teal]{self.client._base_url}[/]")
        console.print(Panel(session_table, title="[ttkia.teal]Session[/]",
                            border_style=_TEAL, padding=(0, 1)))

        # Quick reference
        console.print()
        ref = Table(show_header=False, box=None, padding=(0, 2))
        ref.add_column("category", style=_GREY, width=12)
        ref.add_column("commands")
        ref.add_row("Session", "[ttkia.teal]/new[/]  /quit")
        ref.add_row("Project", "[ttkia.teal]/tree[/]  /map")
        ref.add_row("Context", "[ttkia.teal]/attach[/] <file>  /detach <file>  /attached")
        ref.add_row("Output", "[ttkia.teal]/save[/] [file.md]")
        ref.add_row("Info", "[ttkia.teal]/stats[/]  /id  /help")
        console.print(Panel(ref, title="[bold ttkia.white]Quick Reference[/]",
                            border_style=_PALE, padding=(0, 1)))

        console.print()
        console.print("  [ttkia.dim]Type a task or question to start. Use [ttkia.teal]/help[/ttkia.teal] for details.[/]")
        console.print()

    def _show_stats(self):
        table = Table(show_header=False, box=box.ROUNDED, border_style=_TEAL, padding=(0, 1))
        table.add_column("key", style=_GREY, width=16)
        table.add_column("value", style=_WHITE)
        table.add_row("Conversation", self.conversation_id or "(none)")
        table.add_row("Iterations", str(self._iteration))
        table.add_row("Actions", str(self._total_actions))
        table.add_row("Tokens used", f"{self._total_tokens:,}")
        table.add_row("Attached", f"{len(self._attached)} files ({self._attached_tokens:,} tok)")
        console.print(Panel(table, title="[ttkia.teal]Session Stats[/]",
                            border_style=_TEAL))

    def _show_goodbye(self):
        console.print()
        if self._total_actions > 0 or self._total_tokens > 0:
            table = Table(show_header=False, box=box.ROUNDED, border_style=_TEAL, padding=(0, 1))
            table.add_column("key", style=_GREY, width=20)
            table.add_column("value", style=_WHITE)
            table.add_row("Actions executed", str(self._total_actions))
            table.add_row("Tokens consumed", f"{self._total_tokens:,}")
            table.add_row("Conversation", (self.conversation_id or "n/a")[:16])
            console.print(Panel(table, title="[ttkia.teal]Session Summary[/]",
                                border_style=_TEAL))
        console.print(f"\n  [ttkia.teal]👋 See you later![/]\n")

    def _show_help(self):
        console.print()
        for title, cmds in [
            ("Session", [("/new", "Reset conversation and context"), ("/quit", "Exit efest(OS)")]),
            ("Project", [("/tree", "Show project file tree"), ("/map", "Show repo map (functions, classes)")]),
            ("Context", [("/attach <path>", "Pin file to context"), ("/detach <path>", "Remove from context"), ("/attached", "List pinned files")]),
            ("Output", [("/save [file]", "Save last response to .md file")]),
            ("Info", [("/stats", "Session statistics"), ("/id", "Show conversation ID")]),
        ]:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("cmd", style=_TEAL, width=22)
            table.add_column("desc", style=_GREY)
            for cmd, desc in cmds:
                table.add_row(cmd, desc)
            console.print(Panel(table, title=f"[ttkia.teal]{title}[/]",
                                border_style=_PALE, padding=(0, 1)))
        console.print()
        console.print("  [ttkia.dim]Tip: the agent can read, write, edit, search files and run scripts.[/]")
        console.print("  [ttkia.dim]It also has access to MCP tools (SD-WAN, Meraki, PSIRT, People) for real-time data.[/]")
        console.print()

    # ──────────────────────────────────────────────────────
    # INTERACTIVE LOOP
    # ──────────────────────────────────────────────────────

    def run_interactive(self):
        self._show_banner()

        while True:
            try:
                console.print()
                user_input = console.input("  [bold ttkia.teal]❯[/] ").strip()
            except (KeyboardInterrupt, EOFError):
                self._show_goodbye()
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd = user_input.lower().split()[0]
                if cmd in ("/quit", "/exit", "/q"):
                    self._show_goodbye()
                    break
                elif cmd == "/new":
                    self.conversation_id = None
                    self._iteration = 0
                    self._history = []
                    self._attached = {}
                    self._attached_tokens = 0
                    self._repo_map = ""
                    self._last_response = ""
                    console.print("  [ttkia.info]● New session started[/]")
                elif cmd == "/tree":
                    console.print()
                    console.print(build_rich_tree(self.root, max_depth=3))
                elif cmd == "/map":
                    if not self._repo_map:
                        self._repo_map = build_repo_map(self.root, max_depth=3)
                    console.print()
                    console.print(Syntax(self._repo_map, "markdown", theme="monokai",
                                         padding=(1, 2), background_color=_NAVY))
                elif cmd == "/stats":
                    self._show_stats()
                elif cmd == "/id":
                    console.print(f"  [ttkia.info]● Conversation: {self.conversation_id or '(none)'}[/]")
                elif cmd == "/attach":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        console.print("  [ttkia.warning]⚠ Usage: /attach <file_path>[/]")
                    else:
                        self._attach_file(parts[1].strip())
                elif cmd == "/detach":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        console.print("  [ttkia.warning]⚠ Usage: /detach <file_path>[/]")
                    else:
                        self._detach_file(parts[1].strip())
                elif cmd == "/attached":
                    self._show_attached()
                elif cmd == "/save":
                    parts = user_input.split(maxsplit=1)
                    filename = parts[1].strip() if len(parts) > 1 else None
                    self._save_response(filename)
                elif cmd == "/help":
                    self._show_help()
                else:
                    console.print(f"  [ttkia.warning]⚠ Unknown command: {cmd}  →  /help[/]")
                continue

            try:
                self.ask(user_input)
            except KeyboardInterrupt:
                console.print()
                console.print("  [ttkia.warning]⚠ Interrupted — ready for next query[/]")
            except Exception as e:
                console.print(f"  [ttkia.error]✖ {e}[/]")