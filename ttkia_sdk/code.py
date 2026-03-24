"""
TTKIA Code – Agentic coding assistant with polished terminal UI.

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
import threading
import time
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ttkia_sdk import TTKIAClient


# ═══════════════════════════════════════════════════════════
# TERMINAL UI ENGINE
# ═══════════════════════════════════════════════════════════

class _Term:
    """
    Terminal UI primitives — ANSI 256-color palette aligned with
    Telefónica Tech branding. Disabled if not a TTY.
    """
    _on = sys.stdout.isatty()

    # ── Core escapes ──
    RESET   = "\033[0m"   if _on else ""
    BOLD    = "\033[1m"   if _on else ""
    DIM     = "\033[2m"   if _on else ""
    ITALIC  = "\033[3m"   if _on else ""
    UNDER   = "\033[4m"   if _on else ""

    # ── Telefónica Tech palette (ANSI 256) ──
    TEAL    = "\033[38;5;73m"  if _on else ""   # #67C3C8 – brand accent
    NAVY    = "\033[38;5;236m" if _on else ""   # #141D32 – deep bg
    BLUE    = "\033[38;5;33m"  if _on else ""   # #0066FF – primary
    GREY    = "\033[38;5;103m" if _on else ""   # #8F97AF – secondary
    PALE    = "\033[38;5;146m" if _on else ""   # #B0B6CA – borders
    WHITE   = "\033[38;5;255m" if _on else ""   # text
    AMBER   = "\033[38;5;179m" if _on else ""   # #CDA644 – warning
    CORAL   = "\033[38;5;167m" if _on else ""   # #C96C64 – error
    GREEN   = "\033[38;5;72m"  if _on else ""   # #528889 – success
    CYAN    = "\033[38;5;116m" if _on else ""   # lighter teal for readability

    # ── Background ──
    BG_DARK = "\033[48;5;235m" if _on else ""
    BG_TEAL = "\033[48;5;73m"  if _on else ""
    BG_RED  = "\033[48;5;167m" if _on else ""
    BG_GREEN= "\033[48;5;72m"  if _on else ""

    # ── Box drawing ──
    H_LINE  = "─"
    V_LINE  = "│"
    TL      = "╭"
    TR      = "╮"
    BL      = "╰"
    BR      = "╯"
    ARROW_R = "▸"
    DOT     = "●"
    CHECK   = "✔"
    CROSS   = "✖"
    WARN    = "⚠"

    @classmethod
    def width(cls) -> int:
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    @classmethod
    def line(cls, char: str = "─", color: str = "") -> str:
        c = color or cls.PALE
        return f"{c}{char * cls.width()}{cls.RESET}"

    @classmethod
    def box_top(cls, title: str = "", color: str = "") -> str:
        c = color or cls.TEAL
        if title:
            inner = cls.width() - 4 - len(title)
            return f"{c}{cls.TL}{cls.H_LINE} {cls.BOLD}{title}{cls.RESET}{c} {cls.H_LINE * max(inner, 1)}{cls.TR}{cls.RESET}"
        return f"{c}{cls.TL}{cls.H_LINE * (cls.width() - 2)}{cls.TR}{cls.RESET}"

    @classmethod
    def box_line(cls, text: str, color: str = "") -> str:
        c = color or cls.TEAL
        w = cls.width() - 4
        clean = re.sub(r'\033\[[^m]*m', '', text)
        pad = max(w - len(clean), 0)
        return f"{c}{cls.V_LINE}{cls.RESET} {text}{' ' * pad} {c}{cls.V_LINE}{cls.RESET}"

    @classmethod
    def box_bottom(cls, color: str = "") -> str:
        c = color or cls.TEAL
        return f"{c}{cls.BL}{cls.H_LINE * (cls.width() - 2)}{cls.BR}{cls.RESET}"

    @classmethod
    def panel(cls, title: str, lines: List[str], color: str = ""):
        print(cls.box_top(title, color))
        for line in lines:
            print(cls.box_line(line, color))
        print(cls.box_bottom(color))

    @classmethod
    def section(cls, icon: str, title: str, color: str = ""):
        c = color or cls.TEAL
        w = cls.width() - len(icon) - len(title) - 4
        print(f"\n{c}{cls.H_LINE * 2} {icon} {cls.BOLD}{title}{cls.RESET}{c} {cls.H_LINE * max(w, 1)}{cls.RESET}")

    @classmethod
    def success(cls, msg: str):
        print(f"  {cls.GREEN}{cls.CHECK}{cls.RESET} {msg}")

    @classmethod
    def error(cls, msg: str):
        print(f"  {cls.CORAL}{cls.CROSS}{cls.RESET} {msg}")

    @classmethod
    def warning(cls, msg: str):
        print(f"  {cls.AMBER}{cls.WARN}{cls.RESET} {msg}")

    @classmethod
    def info(cls, msg: str):
        print(f"  {cls.TEAL}{cls.DOT}{cls.RESET} {cls.DIM}{msg}{cls.RESET}")


# ═══════════════════════════════════════════════════════════
# ANIMATED SPINNER
# ═══════════════════════════════════════════════════════════

class Spinner:
    """Animated spinner for long operations."""

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Thinking"):
        self.message = message
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write(f"\r{' ' * (_Term.width())}\r")
        sys.stdout.flush()

    def _spin(self):
        frames = itertools.cycle(self._FRAMES)
        i = 0
        while not self._stop.is_set():
            frame = next(frames)
            dots = "." * (i % 4)
            elapsed = i * 0.1
            timer = f"{_Term.DIM}{elapsed:.0f}s{_Term.RESET}" if elapsed >= 2 else ""
            sys.stdout.write(
                f"\r  {_Term.TEAL}{frame}{_Term.RESET} "
                f"{_Term.WHITE}{self.message}{dots:<3}{_Term.RESET} {timer}"
            )
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.1)


# ═══════════════════════════════════════════════════════════
# MARKDOWN-LITE RENDERER FOR TERMINAL
# ═══════════════════════════════════════════════════════════

def render_markdown(text: str) -> str:
    """Light markdown rendering for terminal output."""
    lines = text.split('\n')
    out = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            if in_code_block:
                lang = line.strip()[3:].strip()
                label = f" {lang} " if lang else ""
                out.append(f"  {_Term.DIM}{_Term.BG_DARK}{label}{'─' * 40}{_Term.RESET}")
            else:
                out.append(f"  {_Term.DIM}{'─' * 44}{_Term.RESET}")
            continue

        if in_code_block:
            out.append(f"  {_Term.BG_DARK} {_Term.CYAN}{line}{_Term.RESET}")
            continue

        if line.startswith('### '):
            out.append(f"\n  {_Term.TEAL}{_Term.BOLD}{line[4:]}{_Term.RESET}")
        elif line.startswith('## '):
            out.append(f"\n  {_Term.TEAL}{_Term.BOLD}{line[3:]}{_Term.RESET}")
        elif line.startswith('# '):
            out.append(f"\n  {_Term.WHITE}{_Term.BOLD}{line[2:]}{_Term.RESET}")
        elif re.match(r'^\s*[-*]\s', line):
            bullet = line.lstrip()
            indent = len(line) - len(bullet)
            content = re.sub(r'^[-*]\s', '', bullet)
            out.append(f"  {'  ' * (indent // 2)}{_Term.TEAL}{_Term.ARROW_R}{_Term.RESET} {_apply_inline(content)}")
        elif re.match(r'^(\s*)\d+\.\s(.+)', line):
            m = re.match(r'^(\s*)\d+\.\s(.+)', line)
            out.append(f"  {m.group(1)}{_Term.TEAL}{_Term.DOT}{_Term.RESET} {_apply_inline(m.group(2))}")
        else:
            out.append(f"  {_apply_inline(line)}")

    return '\n'.join(out)


def _apply_inline(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', f'{_Term.BOLD}\\1{_Term.RESET}', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)\*(?!\*)', f'{_Term.ITALIC}\\1{_Term.RESET}', text)
    text = re.sub(r'`([^`]+)`', f'{_Term.BG_DARK}{_Term.CYAN} \\1 {_Term.RESET}', text)
    text = text.replace('✅', f'{_Term.GREEN}✅{_Term.RESET}')
    return text


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
    for m in re.finditer(r'(?:from|import)\s+([\w.]+)', script):
        module = m.group(1).split('.')[0]
        if module not in _ALLOWED_IMPORTS:
            return f"Blocked import: '{module}' (not in allowed list)"
    return None


# ═══════════════════════════════════════════════════════════
# ACTION PARSER
# ═══════════════════════════════════════════════════════════

# REEMPLAZAR los dos regex y parse_actions por:

_RE_SELF_CLOSE = re.compile(
    r'<action\s+([^>]*?)\s*/\s*>',
    re.DOTALL,
)

_RE_BLOCK = re.compile(
    r'<action\s+([^>]*?)>(?P<body>.*?)</action>',
    re.DOTALL,
)

_RE_ATTR = re.compile(r'(\w+)="([^"]*)"')
_RE_SEARCH = re.compile(r'<search>(.*?)</search>', re.DOTALL)
_RE_REPLACE = re.compile(r'<replace>(.*?)</replace>', re.DOTALL)


def _parse_attrs(attr_str: str) -> dict:
    """Extract key=value attributes from an action tag, order-independent."""
    return dict(_RE_ATTR.findall(attr_str))


def parse_actions(text: str) -> Tuple[str, List[Action]]:
    actions: List[Action] = []

    for m in _RE_SELF_CLOSE.finditer(text):
        attrs = _parse_attrs(m.group(1))
        actions.append(Action(
            type=attrs.get("type", ""),
            path=attrs.get("path"),
            pattern=attrs.get("pattern"),
        ))

    for m in _RE_BLOCK.finditer(text):
        attrs = _parse_attrs(m.group(1))
        body = m.group("body").strip()
        a = Action(
            type=attrs.get("type", ""),
            path=attrs.get("path"),
            pattern=attrs.get("pattern"),
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
    lines = content.split('\n')
    numbered = '\n'.join(f"{i+1:4d} │ {l}" for i, l in enumerate(lines))
    return ActionResult(action=action, success=True,
                        output=f"[{action.path}] ({len(lines)} lines)\n{numbered}")


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
# PROJECT TREE BUILDER
# ═══════════════════════════════════════════════════════════

_SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox',
    '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build',
    '.eggs', '*.egg-info', '.idea', '.vscode',
}
_SKIP_FILES = {'.DS_Store', 'Thumbs.db', '.gitkeep'}

_FILE_ICONS = {
    '.py': '🐍', '.js': '📜', '.ts': '📘', '.yaml': '⚙️ ', '.yml': '⚙️ ',
    '.json': '📋', '.md': '📝', '.html': '🌐', '.css': '🎨',
    '.sh': '🔧', '.toml': '⚙️ ', '.sql': '🗃️ ', '.dockerfile': '🐳',
    '.xlsx': '📊', '.pptx': '📰', '.pdf': '📕',
}


def build_project_tree(root: Path, max_depth: int = 3) -> str:
    lines = [f"{_Term.BOLD}{_Term.TEAL}{root.name}/{_Term.RESET}"]
    _walk_tree(root, "", 0, max_depth, lines)
    return '\n'.join(lines)


def _walk_tree(path: Path, prefix: str, depth: int, max_depth: int, lines: list):
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
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = f"{_Term.PALE}{'└── ' if is_last else '├── '}{_Term.RESET}"
        child_prefix = prefix + f"{_Term.PALE}{'    ' if is_last else '│   '}{_Term.RESET}"
        if entry.is_dir():
            lines.append(f"{prefix}{connector}{_Term.BOLD}{_Term.TEAL}{entry.name}/{_Term.RESET}")
            _walk_tree(entry, child_prefix, depth + 1, max_depth, lines)
        else:
            icon = _FILE_ICONS.get(entry.suffix.lower(), " ")
            size = entry.stat().st_size
            size_str = f"{size:,}B" if size < 1024 else f"{size/1024:.0f}K"
            lines.append(
                f"{prefix}{connector}{icon} {_Term.WHITE}{entry.name}{_Term.RESET}"
                f"  {_Term.DIM}{size_str}{_Term.RESET}"
            )


# ═══════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════

_MAX_ITERATIONS = 20

_ACTION_CONFIG = {
    "read_file":  {"icon": "📖", "label": "READ",   "color": _Term.CYAN,  "confirm": False},
    "write_file": {"icon": "✏️",  "label": "WRITE",  "color": _Term.AMBER, "confirm": True},
    "edit_file":  {"icon": "🔧", "label": "EDIT",   "color": _Term.AMBER, "confirm": True},
    "search":     {"icon": "🔍", "label": "SEARCH", "color": _Term.CYAN,  "confirm": False},
    "run_script": {"icon": "🐍", "label": "SCRIPT", "color": _Term.AMBER, "confirm": True},
}


class CodeAgent:
    """Interactive coding agent — TTKIA backend + local execution."""

    def __init__(self, client: TTKIAClient, root: Path, style: str = "detailed"):
        self.client = client
        self.root = root.resolve()
        self.prompt = 'code_agent'
        self.style = style
        self.conversation_id: Optional[str] = None
        self._iteration = 0
        self._total_tokens = 0
        self._total_actions = 0

    def _build_context_prefix(self) -> str:
        tree = build_project_tree(self.root, max_depth=2)
        clean_tree = re.sub(r'\033\[[^m]*m', '', tree)
        return (
            f"<project_context>\n"
            f"Working directory: {self.root}\n"
            f"Project structure:\n```\n{clean_tree}\n```\n"
            f"</project_context>\n\n"
        )

    def _show_action_header(self, action: Action):
        cfg = _ACTION_CONFIG.get(action.type, {"icon": "⚡", "label": "???", "color": _Term.GREY})
        target = action.path or action.pattern or ""
        print(f"\n  {cfg['icon']} {cfg['color']}{_Term.BOLD}{cfg['label']}{_Term.RESET}"
              f"  {_Term.WHITE}{target}{_Term.RESET}")

    def _show_diff_preview(self, action: Action):
        max_preview = 10
        if action.type == "edit_file" and action.search_text and action.replace_text:
            old_lines = action.search_text.strip().split('\n')
            new_lines = action.replace_text.strip().split('\n')
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")
            for line in old_lines[:max_preview]:
                print(f"  {_Term.CORAL}{_Term.BOLD} - {_Term.RESET}{_Term.CORAL}{line}{_Term.RESET}")
            if len(old_lines) > max_preview:
                print(f"  {_Term.DIM}  ... +{len(old_lines) - max_preview} lines{_Term.RESET}")
            for line in new_lines[:max_preview]:
                print(f"  {_Term.GREEN}{_Term.BOLD} + {_Term.RESET}{_Term.GREEN}{line}{_Term.RESET}")
            if len(new_lines) > max_preview:
                print(f"  {_Term.DIM}  ... +{len(new_lines) - max_preview} lines{_Term.RESET}")
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")

        elif action.type == "write_file" and action.content:
            lines = action.content.split('\n')
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")
            for line in lines[:8]:
                print(f"  {_Term.GREEN}{_Term.BOLD} + {_Term.RESET}{_Term.GREEN}{line}{_Term.RESET}")
            if len(lines) > 8:
                print(f"  {_Term.DIM}  ... +{len(lines) - 8} lines ({len(action.content):,} chars){_Term.RESET}")
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")

        elif action.type == "run_script" and action.content:
            lines = action.content.strip().split('\n')
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")
            for line in lines[:10]:
                print(f"  {_Term.BG_DARK} {_Term.CYAN}{line}{_Term.RESET}")
            if len(lines) > 10:
                print(f"  {_Term.DIM}  ... +{len(lines) - 10} lines{_Term.RESET}")
            print(f"  {_Term.DIM}{'─' * 50}{_Term.RESET}")

    def _confirm_action(self, action: Action) -> bool:
        self._show_diff_preview(action)
        try:
            resp = input(
                f"  {_Term.AMBER}{_Term.BOLD}Apply?{_Term.RESET}"
                f" {_Term.DIM}[{_Term.GREEN}y{_Term.DIM}/{_Term.CORAL}n{_Term.DIM}]{_Term.RESET} "
            ).strip().lower()
            return resp in ("y", "yes", "a", "all", "")
        except (KeyboardInterrupt, EOFError):
            return False

    def _execute_actions(self, actions: List[Action]) -> str:
        results = []
        if actions:
            _Term.section("⚡", f"Actions ({len(actions)})", _Term.TEAL)

        for action in actions:
            cfg = _ACTION_CONFIG.get(action.type, {"confirm": True})
            self._show_action_header(action)

            if cfg.get("confirm"):
                if not self._confirm_action(action):
                    results.append(ActionResult(action=action, success=False,
                                                output="Skipped by user", skipped=True))
                    _Term.warning("Skipped")
                    continue

            result = execute_action(action, self.root)
            results.append(result)
            self._total_actions += 1

            if result.success:
                if action.type == "read_file":
                    _Term.success(f"Read {result.output.count(chr(10))} lines")
                elif action.type == "search":
                    _Term.success(result.output.split('\n')[0])
                else:
                    _Term.success(result.output)
            else:
                _Term.error(result.output)

        parts = []
        for r in results:
            status = "SUCCESS" if r.success else ("SKIPPED" if r.skipped else "ERROR")
            parts.append(
                f"<action_result type=\"{r.action.type}\" "
                f"path=\"{r.action.path or ''}\" "
                f"status=\"{status}\">\n{r.output}\n</action_result>"
            )
        return '\n\n'.join(parts)

    def ask(self, user_query: str) -> str:
        self._iteration = 0
        if self.conversation_id is None:
            query = self._build_context_prefix() + f"<user_request>\n{user_query}\n</user_request>"
        else:
            query = user_query

        final_prose = ""
        while self._iteration < _MAX_ITERATIONS:
            self._iteration += 1
            
            with Spinner(f"Thinking (step {self._iteration})"):
                try:
                    response = self.client.code_query(
                        query,
                        conversation_id=self.conversation_id,
                        title="[TTKIA Code]" if not self.conversation_id else None,
                    )
                except Exception as e:
                    _Term.error(str(e))
                    return ""

            # Manejar errores del backend
            if response.is_error:
                _Term.error(response.error or "Backend returned an error")
                return ""

            if not self.conversation_id:
                self.conversation_id = response.conversation_id
            self._total_tokens += response.token_usage.total

            prose, actions = parse_actions(response.text)
            if prose:
                _Term.section("💬", "Response", _Term.WHITE)
                print(render_markdown(prose))
            final_prose = prose

            if not actions:
                return prose

            feedback = self._execute_actions(actions)
            query = (
                f"<original_request>{user_query}</original_request>\n\n"
                f"<action_results>\n{feedback}\n</action_results>\n\n"
                f"The original user request is shown above. "
                f"Continue with ONLY what was requested. Do NOT add features or changes that were not asked for. "
                f"If the task is complete, summarize what was found or done."
            )


        _Term.error(f"Reached max iterations ({_MAX_ITERATIONS})")
        return final_prose

    def _show_banner(self):
        print()
        print(_Term.line("═", _Term.TEAL))
        print()
        print(f"  {_Term.TEAL}{_Term.BOLD}  ████████╗████████╗██╗  ██╗██╗ █████╗ {_Term.RESET}")
        print(f"  {_Term.TEAL}{_Term.BOLD}  ╚══██╔══╝╚══██╔══╝██║ ██╔╝██║██╔══██╗{_Term.RESET}")
        print(f"  {_Term.TEAL}{_Term.BOLD}     ██║      ██║   █████╔╝ ██║███████║{_Term.RESET}")
        print(f"  {_Term.TEAL}{_Term.BOLD}     ██║      ██║   ██╔═██╗ ██║██╔══██║{_Term.RESET}")
        print(f"  {_Term.TEAL}{_Term.BOLD}     ██║      ██║   ██║  ██╗██║██║  ██║{_Term.RESET}")
        print(f"  {_Term.TEAL}{_Term.BOLD}     ╚═╝      ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝{_Term.RESET}")
        print(f"  {_Term.WHITE}{_Term.BOLD}                  C O D E{_Term.RESET}")
        print()
        print(_Term.line("═", _Term.TEAL))
        print()

        _Term.panel("Session", [
            f"{_Term.GREY}Project   {_Term.WHITE}{self.root.name}/{_Term.RESET}",
            f"{_Term.GREY}Path      {_Term.DIM}{self.root}{_Term.RESET}",
            f"{_Term.GREY}Backend   {_Term.TEAL}{self.client._base_url}{_Term.RESET}",
        ])
        print()
        print(f"  {_Term.DIM}Commands:  "
              f"{_Term.TEAL}/quit{_Term.DIM}  "
              f"{_Term.TEAL}/new{_Term.DIM}  "
              f"{_Term.TEAL}/tree{_Term.DIM}  "
              f"{_Term.TEAL}/stats{_Term.DIM}  "
              f"{_Term.TEAL}/id{_Term.DIM}  "
              f"{_Term.TEAL}/help{_Term.RESET}")
        print()

    def _show_stats(self):
        _Term.panel("Session Stats", [
            f"{_Term.GREY}Conversation  {_Term.WHITE}{self.conversation_id or '(none)'}{_Term.RESET}",
            f"{_Term.GREY}Iterations    {_Term.WHITE}{self._iteration}{_Term.RESET}",
            f"{_Term.GREY}Actions       {_Term.WHITE}{self._total_actions}{_Term.RESET}",
            f"{_Term.GREY}Tokens        {_Term.WHITE}{self._total_tokens:,}{_Term.RESET}",
        ])

    def _show_goodbye(self):
        print()
        if self._total_actions > 0:
            _Term.panel("Session Summary", [
                f"{_Term.GREY}Actions executed  {_Term.WHITE}{self._total_actions}{_Term.RESET}",
                f"{_Term.GREY}Tokens used       {_Term.WHITE}{self._total_tokens:,}{_Term.RESET}",
                f"{_Term.GREY}Conversation      {_Term.WHITE}{(self.conversation_id or 'n/a')[:12]}{_Term.RESET}",
            ], _Term.TEAL)
        print(f"\n  {_Term.TEAL}Bye! 👋{_Term.RESET}\n")

    def run_interactive(self):
        self._show_banner()

        while True:
            try:
                print()
                user_input = input(f"  {_Term.TEAL}{_Term.BOLD}❯{_Term.RESET} ").strip()
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
                    _Term.info("New session started")
                elif cmd == "/tree":
                    print()
                    print(build_project_tree(self.root, max_depth=3))
                elif cmd == "/stats":
                    self._show_stats()
                elif cmd == "/id":
                    _Term.info(f"Conversation: {self.conversation_id or '(none)'}")
                elif cmd == "/help":
                    _Term.panel("Commands", [
                        f"{_Term.TEAL}/quit{_Term.RESET}    Exit TTKIA Code",
                        f"{_Term.TEAL}/new{_Term.RESET}     Start fresh session",
                        f"{_Term.TEAL}/tree{_Term.RESET}    Show project structure",
                        f"{_Term.TEAL}/stats{_Term.RESET}   Session statistics",
                        f"{_Term.TEAL}/id{_Term.RESET}      Show conversation ID",
                    ])
                else:
                    _Term.warning(f"Unknown command: {cmd}  →  /help")
                continue

            try:
                self.ask(user_input)
            except KeyboardInterrupt:
                print()
                _Term.warning("Interrupted — ready for next query")
            except Exception as e:
                _Term.error(f"{e}")