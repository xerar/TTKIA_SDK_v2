"""
TTKIA Code – Agentic coding assistant.

Runs as an interactive CLI loop:
1. User describes a task
2. TTKIA backend (Claude) responds with structured actions
3. CLI executes actions locally (read/write files, search project)
4. Results feed back into the conversation for next iteration

Actions are communicated via XML blocks in the response:
  <action type="read_file" path="src/main.py" />
  <action type="write_file" path="src/new.py">content</action>
  <action type="edit_file" path="src/main.py">
    <search>old code</search>
    <replace>new code</replace>
  </action>
  <action type="search" pattern="def calculate" path="src/" />
"""

from __future__ import annotations

import os
import re
import glob
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ttkia_sdk import TTKIAClient


# ═══════════════════════════════════════════════════════════
# ANSI COLORS
# ═══════════════════════════════════════════════════════════

class _C:
    """ANSI codes – disabled if not a TTY."""
    import sys
    _on = sys.stdout.isatty()

    BOLD    = "\033[1m"   if _on else ""
    DIM     = "\033[2m"   if _on else ""
    GREEN   = "\033[32m"  if _on else ""
    YELLOW  = "\033[33m"  if _on else ""
    CYAN    = "\033[36m"  if _on else ""
    RED     = "\033[31m"  if _on else ""
    MAGENTA = "\033[35m"  if _on else ""
    WHITE   = "\033[97m"  if _on else ""
    RESET   = "\033[0m"   if _on else ""


# ═══════════════════════════════════════════════════════════
# ACTION MODELS
# ═══════════════════════════════════════════════════════════

@dataclass
class Action:
    """A single action parsed from the model response."""
    type: str               # read_file, write_file, edit_file, search
    path: Optional[str] = None
    content: Optional[str] = None
    search_text: Optional[str] = None
    replace_text: Optional[str] = None
    pattern: Optional[str] = None


@dataclass
class ActionResult:
    """Result of executing an action locally."""
    action: Action
    success: bool
    output: str
    skipped: bool = False


# ═══════════════════════════════════════════════════════════
# SYSTEM PROMPT (injected into user query for /query_complete)
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are TTKIA Code, an expert coding assistant operating through a CLI agent.
You help developers by reading, writing, editing files, and searching codebases.

IMPORTANT: You communicate file operations using XML action blocks. The CLI will parse and execute them locally.

## Available actions

### Read a file
```
<action type="read_file" path="relative/path/to/file" />
```

### Write/create a file
```
<action type="write_file" path="relative/path/to/file">
file content here
</action>
```

### Edit a file (search & replace)
```
<action type="edit_file" path="relative/path/to/file">
<search>exact text to find</search>
<replace>replacement text</replace>
</action>
```

### Search in project (grep)
```
<action type="search" pattern="search pattern" path="directory/" />
```

## Rules
1. ALWAYS read files before editing them — never guess content.
2. Use edit_file with exact search text (copy from read output). Keep edits minimal.
3. You can use multiple actions in one response.
4. Explain what you're doing BEFORE the action blocks.
5. After actions execute, you'll receive the results and can continue.
6. For search, use regex patterns compatible with grep -rn.
7. Paths are relative to the project root (working directory).
8. When the task is complete, say "✅ Done" clearly.
"""


# ═══════════════════════════════════════════════════════════
# ACTION PARSER
# ═══════════════════════════════════════════════════════════

# Regex for self-closing: <action type="..." path="..." />
_RE_SELF_CLOSE = re.compile(
    r'<action\s+type="(?P<type>[^"]+)"'
    r'(?:\s+path="(?P<path>[^"]*)")?'
    r'(?:\s+pattern="(?P<pattern>[^"]*)")?'
    r'\s*/\s*>',
    re.DOTALL,
)

# Regex for block: <action type="..." path="...">...</action>
_RE_BLOCK = re.compile(
    r'<action\s+type="(?P<type>[^"]+)"'
    r'(?:\s+path="(?P<path>[^"]*)")?'
    r'(?:\s+pattern="(?P<pattern>[^"]*)")?'
    r'\s*>(?P<body>.*?)</action>',
    re.DOTALL,
)

# For edit_file: extract <search> and <replace> from body
_RE_SEARCH = re.compile(r'<search>(.*?)</search>', re.DOTALL)
_RE_REPLACE = re.compile(r'<replace>(.*?)</replace>', re.DOTALL)


def parse_actions(text: str) -> Tuple[str, List[Action]]:
    """
    Parse model response text into prose + list of actions.
    Returns (prose_text, actions).
    """
    actions: List[Action] = []

    # Parse self-closing actions
    for m in _RE_SELF_CLOSE.finditer(text):
        actions.append(Action(
            type=m.group("type"),
            path=m.group("path"),
            pattern=m.group("pattern"),
        ))

    # Parse block actions
    for m in _RE_BLOCK.finditer(text):
        a = Action(
            type=m.group("type"),
            path=m.group("path"),
            pattern=m.group("pattern"),
        )
        body = m.group("body").strip()

        if a.type == "edit_file":
            sm = _RE_SEARCH.search(body)
            rm = _RE_REPLACE.search(body)
            a.search_text = sm.group(1) if sm else None
            a.replace_text = rm.group(1) if rm else None
        elif a.type == "write_file":
            a.content = body

        actions.append(a)

    # Strip action blocks from text to get prose
    prose = _RE_BLOCK.sub("", text)
    prose = _RE_SELF_CLOSE.sub("", prose)
    prose = re.sub(r'\n{3,}', '\n\n', prose).strip()

    return prose, actions


# ═══════════════════════════════════════════════════════════
# LOCAL EXECUTOR
# ═══════════════════════════════════════════════════════════

_MAX_READ_SIZE = 100_000  # ~100KB max file read
_MAX_SEARCH_RESULTS = 50


def _resolve_path(path: str, root: Path) -> Path:
    """Resolve relative path against project root, with safety check."""
    resolved = (root / path).resolve()
    # Prevent path traversal outside root
    if not str(resolved).startswith(str(root.resolve())):
        raise ValueError(f"Path traversal blocked: {path}")
    return resolved


def execute_action(action: Action, root: Path, auto_read: bool = True) -> ActionResult:
    """
    Execute a single action locally.
    
    Args:
        action: The action to execute
        root: Project root directory
        auto_read: If True, read/search actions execute without confirmation
    """
    try:
        if action.type == "read_file":
            return _exec_read(action, root)
        elif action.type == "write_file":
            return _exec_write(action, root)
        elif action.type == "edit_file":
            return _exec_edit(action, root)
        elif action.type == "search":
            return _exec_search(action, root)
        else:
            return ActionResult(action=action, success=False,
                                output=f"Unknown action type: {action.type}")
    except Exception as e:
        return ActionResult(action=action, success=False, output=f"Error: {e}")


def _exec_read(action: Action, root: Path) -> ActionResult:
    """Read a file and return its contents."""
    fp = _resolve_path(action.path, root)

    if not fp.exists():
        return ActionResult(action=action, success=False,
                            output=f"File not found: {action.path}")

    if fp.stat().st_size > _MAX_READ_SIZE:
        return ActionResult(action=action, success=False,
                            output=f"File too large ({fp.stat().st_size} bytes). Max: {_MAX_READ_SIZE}")

    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ActionResult(action=action, success=False,
                            output=f"Binary file, cannot read as text: {action.path}")

    # Add line numbers
    lines = content.split('\n')
    numbered = '\n'.join(f"{i+1:4d} │ {line}" for i, line in enumerate(lines))

    return ActionResult(action=action, success=True,
                        output=f"[{action.path}] ({len(lines)} lines)\n{numbered}")


def _exec_write(action: Action, root: Path) -> ActionResult:
    """Write/create a file."""
    fp = _resolve_path(action.path, root)

    # Create parent directories if needed
    fp.parent.mkdir(parents=True, exist_ok=True)

    existed = fp.exists()
    fp.write_text(action.content or "", encoding="utf-8")

    verb = "Updated" if existed else "Created"
    lines = (action.content or "").count('\n') + 1
    return ActionResult(action=action, success=True,
                        output=f"{verb} {action.path} ({lines} lines)")


def _exec_edit(action: Action, root: Path) -> ActionResult:
    """Edit a file using search & replace."""
    fp = _resolve_path(action.path, root)

    if not fp.exists():
        return ActionResult(action=action, success=False,
                            output=f"File not found: {action.path}")

    if not action.search_text:
        return ActionResult(action=action, success=False,
                            output="edit_file requires <search> block")

    content = fp.read_text(encoding="utf-8")

    # Count occurrences
    count = content.count(action.search_text)
    if count == 0:
        # Show a snippet of the file to help debug
        return ActionResult(action=action, success=False,
                            output=f"Search text not found in {action.path}. "
                                   f"File has {len(content)} chars. "
                                   f"Read the file first to get exact content.")
    if count > 1:
        return ActionResult(action=action, success=False,
                            output=f"Search text found {count} times in {action.path}. "
                                   f"Make the search text more specific (unique).")

    new_content = content.replace(action.search_text, action.replace_text or "", 1)
    fp.write_text(new_content, encoding="utf-8")

    return ActionResult(action=action, success=True,
                        output=f"Edited {action.path}: replaced 1 occurrence "
                               f"({len(action.search_text)} chars → {len(action.replace_text or '')} chars)")


def _exec_search(action: Action, root: Path) -> ActionResult:
    """Search project files using grep."""
    search_path = action.path or "."
    target = _resolve_path(search_path, root)

    if not target.exists():
        return ActionResult(action=action, success=False,
                            output=f"Search path not found: {search_path}")

    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.yaml",
             "--include=*.yml", "--include=*.json", "--include=*.md",
             "--include=*.html", "--include=*.css", "--include=*.sh",
             "--include=*.toml", "--include=*.cfg", "--include=*.txt",
             "-E", action.pattern or "", str(target)],
            capture_output=True, text=True, timeout=10,
            cwd=str(root),
        )
    except subprocess.TimeoutExpired:
        return ActionResult(action=action, success=False,
                            output="Search timed out (10s limit)")

    lines = result.stdout.strip().split('\n') if result.stdout.strip() else []

    if not lines:
        return ActionResult(action=action, success=True,
                            output=f"No matches for pattern: {action.pattern}")

    # Truncate if too many results
    total = len(lines)
    if total > _MAX_SEARCH_RESULTS:
        lines = lines[:_MAX_SEARCH_RESULTS]

    # Make paths relative
    root_str = str(root) + "/"
    output_lines = [line.replace(root_str, "") for line in lines]
    output = '\n'.join(output_lines)

    if total > _MAX_SEARCH_RESULTS:
        output += f"\n... ({total - _MAX_SEARCH_RESULTS} more results truncated)"

    return ActionResult(action=action, success=True,
                        output=f"Found {total} matches:\n{output}")


# ═══════════════════════════════════════════════════════════
# PROJECT CONTEXT BUILDER
# ═══════════════════════════════════════════════════════════

# Directories/patterns to skip when building tree
_SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox',
    '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build',
    '.eggs', '*.egg-info', '.idea', '.vscode',
}

_SKIP_FILES = {'.DS_Store', 'Thumbs.db', '.gitkeep'}


def build_project_tree(root: Path, max_depth: int = 3) -> str:
    """
    Build a compact directory tree of the project.
    Used as initial context for the agent.
    """
    lines = [f"{root.name}/"]
    _walk_tree(root, "", 0, max_depth, lines)
    return '\n'.join(lines)


def _walk_tree(path: Path, prefix: str, depth: int, max_depth: int, lines: list):
    """Recursive tree walker."""
    if depth >= max_depth:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    # Filter
    entries = [
        e for e in entries
        if e.name not in _SKIP_FILES
        and not any(e.name == sd or (sd.startswith('*') and e.name.endswith(sd[1:]))
                    for sd in _SKIP_DIRS)
        and not e.name.startswith('.')
    ]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            _walk_tree(entry, child_prefix, depth + 1, max_depth, lines)
        else:
            lines.append(f"{prefix}{connector}{entry.name}")


# ═══════════════════════════════════════════════════════════
# AGENT LOOP
# ═══════════════════════════════════════════════════════════

_MAX_ITERATIONS = 20  # Safety limit for agentic loop


class CodeAgent:
    """
    Interactive coding agent that uses TTKIA as backend brain
    and executes file operations locally.
    """

    def __init__(self, client: TTKIAClient, root: Path, prompt: str = "default",
                 style: str = "detailed"):
        self.client = client
        self.root = root.resolve()
        self.prompt = prompt
        self.style = style
        self.conversation_id: Optional[str] = None
        self._iteration = 0

    def _build_context_prefix(self) -> str:
        """Build project context that gets prepended to the first query."""
        tree = build_project_tree(self.root, max_depth=2)
        return (
            f"<system_instructions>\n{SYSTEM_PROMPT}\n</system_instructions>\n\n"
            f"<project_context>\n"
            f"Working directory: {self.root}\n"
            f"Project structure:\n```\n{tree}\n```\n"
            f"</project_context>\n\n"
        )

    def _confirm_action(self, action: Action) -> bool:
        """Ask user to confirm a write/edit action."""
        if action.type == "write_file":
            lines = (action.content or "").count('\n') + 1
            print(f"\n{_C.YELLOW}  ⚠ Write {action.path} ({lines} lines){_C.RESET}")
            if action.content and len(action.content) < 2000:
                # Show preview for small files
                preview = action.content[:500]
                if len(action.content) > 500:
                    preview += f"\n... ({len(action.content) - 500} more chars)"
                print(f"{_C.DIM}{preview}{_C.RESET}")
        elif action.type == "edit_file":
            print(f"\n{_C.YELLOW}  ⚠ Edit {action.path}{_C.RESET}")
            if action.search_text:
                print(f"{_C.RED}  - {action.search_text[:200]}{_C.RESET}")
            if action.replace_text:
                print(f"{_C.GREEN}  + {action.replace_text[:200]}{_C.RESET}")

        try:
            resp = input(f"{_C.YELLOW}  Apply? [y/n/a(ll)]: {_C.RESET}").strip().lower()
            return resp in ("y", "yes", "a", "all")
        except (KeyboardInterrupt, EOFError):
            return False

    def _execute_actions(self, actions: List[Action]) -> str:
        """Execute a list of actions and return formatted results."""
        results = []

        for action in actions:
            # Read & search: auto-execute. Write & edit: confirm.
            needs_confirm = action.type in ("write_file", "edit_file")

            if needs_confirm:
                if not self._confirm_action(action):
                    results.append(ActionResult(
                        action=action, success=False,
                        output=f"Skipped by user: {action.type} {action.path}",
                        skipped=True,
                    ))
                    continue

            # Icon per type
            icons = {
                "read_file": "📖",
                "write_file": "✏️",
                "edit_file": "🔧",
                "search": "🔍",
            }
            icon = icons.get(action.type, "⚡")
            print(f"  {icon} {_C.DIM}{action.type}: {action.path or action.pattern}{_C.RESET}")

            result = execute_action(action, self.root)
            results.append(result)

            # Show result status
            if result.success:
                # For reads, show abbreviated output
                if action.type == "read_file":
                    line_count = result.output.count('\n')
                    print(f"     {_C.GREEN}✓ Read {line_count} lines{_C.RESET}")
                elif action.type == "search":
                    match_count = result.output.split('\n')[0] if result.output else "0 matches"
                    print(f"     {_C.GREEN}✓ {match_count}{_C.RESET}")
                else:
                    print(f"     {_C.GREEN}✓ {result.output}{_C.RESET}")
            else:
                print(f"     {_C.RED}✗ {result.output}{_C.RESET}")

        # Format results for feeding back to the model
        feedback_parts = []
        for r in results:
            status = "SUCCESS" if r.success else ("SKIPPED" if r.skipped else "ERROR")
            feedback_parts.append(
                f"<action_result type=\"{r.action.type}\" "
                f"path=\"{r.action.path or ''}\" "
                f"status=\"{status}\">\n{r.output}\n</action_result>"
            )

        return '\n\n'.join(feedback_parts)

    def ask(self, user_query: str) -> str:
        """
        Send a query through the agentic loop.
        Returns the final prose response.
        """
        self._iteration = 0

        # First message: include project context
        if self.conversation_id is None:
            query = self._build_context_prefix() + f"<user_request>\n{user_query}\n</user_request>"
        else:
            query = user_query

        while self._iteration < _MAX_ITERATIONS:
            self._iteration += 1

            # Call TTKIA backend
            print(f"\n{_C.DIM}  ⏳ Thinking... (iteration {self._iteration}){_C.RESET}")
            response = self.client.query(
                query,
                conversation_id=self.conversation_id,
                prompt=self.prompt,
                style=self.style,
                title="[TTKIA Code]" if not self.conversation_id else None,
            )

            # Track conversation
            if not self.conversation_id:
                self.conversation_id = response.conversation_id

            # Parse response
            prose, actions = parse_actions(response.text)

            # Show prose to user
            if prose:
                print(f"\n{_C.WHITE}{prose}{_C.RESET}")

            # If no actions, we're done (model just replied with text)
            if not actions:
                return prose

            # Execute actions
            feedback = self._execute_actions(actions)

            # Feed results back as next query
            query = (
                f"<action_results>\n{feedback}\n</action_results>\n\n"
                f"Continue with the task. If complete, summarize what was done."
            )

        print(f"\n{_C.RED}⚠ Reached max iterations ({_MAX_ITERATIONS}). Stopping.{_C.RESET}")
        return prose

    def run_interactive(self):
        """Main interactive loop."""
        print(f"\n{_C.BOLD}🔧 TTKIA Code{_C.RESET}")
        print(f"{_C.DIM}   Project: {self.root}")
        print(f"   Commands: /quit  /new  /tree  /id  /help{_C.RESET}")
        print()

        while True:
            try:
                user_input = input(f"{_C.CYAN}ttkia-code ❯{_C.RESET} ").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{_C.DIM}Bye!{_C.RESET}")
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                cmd = user_input.lower().split()[0]
                if cmd in ("/quit", "/exit", "/q"):
                    break
                elif cmd == "/new":
                    self.conversation_id = None
                    print(f"{_C.DIM}  ↻ New session{_C.RESET}")
                    continue
                elif cmd == "/tree":
                    tree = build_project_tree(self.root, max_depth=3)
                    print(f"{_C.DIM}{tree}{_C.RESET}")
                    continue
                elif cmd == "/id":
                    print(f"{_C.DIM}  Conversation: {self.conversation_id or '(none)'}{_C.RESET}")
                    continue
                elif cmd == "/help":
                    print(f"{_C.DIM}  /quit    Exit")
                    print(f"  /new     Start new session (reset conversation)")
                    print(f"  /tree    Show project structure")
                    print(f"  /id      Show conversation ID{_C.RESET}")
                    continue
                else:
                    print(f"{_C.DIM}  Unknown command. Type /help{_C.RESET}")
                    continue

            # Regular query → agentic loop
            try:
                self.ask(user_input)
            except KeyboardInterrupt:
                print(f"\n{_C.YELLOW}  ⏹ Interrupted{_C.RESET}")
            except Exception as e:
                print(f"\n{_C.RED}  ❌ Error: {e}{_C.RESET}")