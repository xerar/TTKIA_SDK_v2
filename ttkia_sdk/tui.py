"""
efest(OS) — TUI interface for TTKIA Code.

A Textual-based terminal application with 3-panel layout:
  - Left panel:  Project file tree (navigable, lazy-load)
  - Right panel: Chat log (scrollable, Rich renderables)
  - Bottom:      Input bar + status footer

Reuses the entire CodeAgent engine from code.py.

Launch:  ttkia code          → TUI mode (this file)
         ttkia code "query"  → one-shot mode (code.py Rich CLI, unchanged)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (
    Footer,
    Input,
    RichLog,
    Static,
    Tree,
)
from textual.widgets._tree import TreeNode

from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table
from rich import box

from ttkia_sdk.code import (
    CodeAgent,
    build_repo_map,
    parse_actions,
    execute_action,
    _ACTION_STYLE,
    _SKIP_DIRS,
    _SKIP_FILES,
    _FILE_ICONS,
)


# ═══════════════════════════════════════════════════════════
# THEME — sober light palette
# ═══════════════════════════════════════════════════════════

_SLATE   = "#475569"
_STONE   = "#78716C"
_LIGHT   = "#F8FAFC"
_SURFACE = "#F1F5F9"
_PANEL   = "#E2E8F0"
_BORDER  = "#CBD5E1"
_FG      = "#1E293B"
_MUTED   = "#64748B"
_ACCENT  = "#3B82F6"
_GREEN   = "#16A34A"
_AMBER   = "#D97706"
_RED     = "#DC2626"

efestos_theme = Theme(
    name="efestos",
    primary=_SLATE,
    secondary=_STONE,
    accent=_ACCENT,
    foreground=_FG,
    background=_LIGHT,
    success=_GREEN,
    warning=_AMBER,
    error=_RED,
    surface=_SURFACE,
    panel=_PANEL,
    dark=False,
)


# ═══════════════════════════════════════════════════════════
# PROJECT TREE
# ═══════════════════════════════════════════════════════════

class ProjectTree(Tree):
    """File tree for the project directory — lazy-loaded."""

    DEFAULT_CSS = """
    ProjectTree {
        width: 42;
        min-width: 28;
        max-width: 60;
        padding: 1 0 0 1;
        scrollbar-size: 1 1;
        background: $panel;
        border-right: solid $secondary 30%;
    }
    ProjectTree:focus > .tree--cursor {
        background: $accent 20%;
    }
    ProjectTree > .tree--cursor {
        background: $primary 10%;
    }
    """

    def __init__(self, root_path: Path) -> None:
        self._root_path = root_path.resolve()
        super().__init__(root_path.name, id="project-tree")
        self.guide_style = _BORDER
        self.root.expand()

    def on_mount(self) -> None:
        self._populate_node(self.root, self._root_path)

    def _populate_node(self, node: TreeNode, path: Path) -> None:
        try:
            entries = sorted(
                path.iterdir(),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.name in _SKIP_FILES:
                continue
            if any(
                entry.name == sd or (sd.startswith("*") and entry.name.endswith(sd[1:]))
                for sd in _SKIP_DIRS
            ):
                continue
            if entry.is_dir():
                child = node.add(entry.name + "/", data=entry, expand=False)
                child.allow_expand = True
                child.add_leaf("…")
            else:
                node.add_leaf(entry.name, data=entry)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        path = node.data
        if path is None or not isinstance(path, Path) or not path.is_dir():
            return
        children_labels = [str(c.label) for c in node.children]
        if children_labels == ["…"]:
            node.remove_children()
            self._populate_node(node, path)


# ═══════════════════════════════════════════════════════════
# TITLE BAR (includes session stats on the right)
# ═══════════════════════════════════════════════════════════

class TitleBar(Static):
    """Minimal 1-line top bar with stats."""

    DEFAULT_CSS = """
    TitleBar {
        dock: top;
        height: 1;
        background: $primary;
        color: $background;
        text-style: bold;
        padding: 0 2;
    }
    """

    def __init__(self, project_name: str = "") -> None:
        self._project = project_name
        super().__init__(self._format(project_name))

    def _format(self, project: str, cid: str = "", tokens: int = 0, actions: int = 0) -> str:
        left = f" efest(OS)  ·  {project}"
        short_cid = cid[:8] if cid else "—"
        right = f"{short_cid}  ·  {actions} act  ·  {tokens:,} tok "
        return left + "  " * 4 + right

    def set_info(self, *, cid: str = "", tokens: int = 0, actions: int = 0) -> None:
        self.update(self._format(self._project, cid, tokens, actions))


# ═══════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════

class EfestOSApp(App):
    """efest(OS) — TTKIA Code TUI."""

    TITLE = "efest(OS)"

    CSS = """
    Screen {
        background: $background;
    }

    #main-area {
        height: 1fr;
    }

    #chat-panel {
        width: 1fr;
    }

    #chat-log {
        height: 1fr;
        scrollbar-size: 1 1;
        padding: 1 2;
        background: $background;
    }

    #input-bar {
        height: 7;
        padding: 1 2 2 2;
        background: $surface;
        border-top: solid $secondary 20%;
    }

    #query-input {
        width: 1fr;
        border: round $secondary 40%;
        background: $background;
    }
    #query-input:focus {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New", show=True),
        Binding("ctrl+t", "toggle_tree", "Tree", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("f1", "show_help", "Help", show=True),
        Binding("f5", "refresh_tree", "Refresh", show=True),
    ]

    def __init__(self, agent: CodeAgent, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent = agent
        self._tree_visible = True

    def on_mount(self) -> None:
        self.register_theme(efestos_theme)
        self.theme = "efestos"
        self._refresh_status()
        chat = self.query_one("#chat-log", RichLog)
        chat.write(
            Text.from_markup(
                f"[bold]efest(OS)[/]  powered by TTKIA\n\n"
                f"[dim]Project:[/]  {self.agent.root.name}/\n"
                f"[dim]Backend:[/]  {self.agent.client._base_url}\n\n"
                f"[dim]Type a task below or press F1 for help.[/]"
            )
        )

    def compose(self) -> ComposeResult:
        yield TitleBar(self.agent.root.name)
        with Horizontal(id="main-area"):
            yield ProjectTree(self.agent.root)
            with Vertical(id="chat-panel"):
                yield RichLog(
                    id="chat-log",
                    highlight=True,
                    markup=True,
                    wrap=True,
                    auto_scroll=True,
                )
        with Vertical(id="input-bar"):
            yield Input(placeholder="Describe your coding task…", id="query-input")
        yield Footer()

    # ──────────────────────────────────────────────────────
    # INPUT
    # ──────────────────────────────────────────────────────

    @on(Input.Submitted, "#query-input")
    def handle_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        if text.startswith("/"):
            self._slash_command(text)
        else:
            self._run_query(text)

    # ──────────────────────────────────────────────────────
    # SLASH COMMANDS
    # ──────────────────────────────────────────────────────

    def _slash_command(self, raw: str) -> None:
        chat = self.query_one("#chat-log", RichLog)
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/new":
            self.action_new_session()
        elif cmd == "/tree":
            self.action_toggle_tree()
        elif cmd == "/map":
            if not self.agent._repo_map:
                self.agent._repo_map = build_repo_map(self.agent.root, max_depth=3)
            chat.write(
                Syntax(self.agent._repo_map, "markdown", theme="default",
                       padding=(1, 2))
            )
        elif cmd == "/stats":
            self._show_stats()
        elif cmd == "/id":
            cid = self.agent.conversation_id or "(none)"
            chat.write(Text(f"  Conversation: {cid}", style="dim"))
        elif cmd == "/attach":
            if arg:
                self.agent._attach_file(arg)
                self._show_attached()
            else:
                chat.write(Text("  Usage: /attach <path>", style=f"dim {_AMBER}"))
        elif cmd == "/detach":
            if arg:
                self.agent._detach_file(arg)
            else:
                chat.write(Text("  Usage: /detach <path>", style=f"dim {_AMBER}"))
        elif cmd == "/attached":
            self._show_attached()
        elif cmd == "/save":
            self.agent._save_response(arg if arg else None)
        elif cmd == "/help":
            self.action_show_help()
        else:
            chat.write(Text(f"  Unknown: {cmd}  — try /help", style=f"dim {_AMBER}"))

    # ──────────────────────────────────────────────────────
    # AGENT QUERY (background thread)
    # ──────────────────────────────────────────────────────

    @work(exclusive=True, thread=True)
    def _run_query(self, user_query: str) -> None:
        chat = self.query_one("#chat-log", RichLog)

        # User prompt
        self.call_from_thread(
            chat.write,
            Text(f"\n▸ {user_query}", style=f"bold {_FG}"),
        )
        self.call_from_thread(
            chat.write,
            Text("  Thinking…", style="dim italic"),
        )

        try:
            self.agent._iteration = 0

            if self.agent.conversation_id is None:
                query = (
                    self.agent._build_context_prefix()
                    + f"<user_request>\n{user_query}\n</user_request>"
                )
            else:
                query = user_query

            while self.agent._iteration < 20:
                self.agent._iteration += 1
                full_text = ""
                token_counts = {}

                try:
                    for event in self.agent.client.code_query_stream(
                        query,
                        conversation_id=self.agent.conversation_id,
                        title="[efestOS]" if not self.agent.conversation_id else None,
                    ):
                        etype = event.get("type", "")
                        if etype == "mcp":
                            self.call_from_thread(
                                chat.write,
                                Text(f"  ↳ {event.get('content', '')}", style="dim"),
                            )
                        elif etype == "text":
                            full_text += event.get("content", "")
                        elif etype == "done":
                            if not self.agent.conversation_id:
                                self.agent.conversation_id = event.get("conversation_id")
                            token_counts = event.get("token_counts", {})
                            self.agent._total_tokens += (
                                token_counts.get("input", 0)
                                + token_counts.get("output", 0)
                            )
                        elif etype == "error":
                            self.call_from_thread(
                                chat.write,
                                Text(f"  Error: {event.get('content', '?')}", style=f"bold {_RED}"),
                            )
                            self.call_from_thread(self._refresh_status)
                            return
                except Exception as e:
                    self.call_from_thread(
                        chat.write,
                        Text(f"  Error: {e}", style=f"bold {_RED}"),
                    )
                    self.call_from_thread(self._refresh_status)
                    return

                prose, actions = parse_actions(full_text)

                if prose:
                    out_tok = token_counts.get("output", "")
                    step = self.agent._iteration
                    self.call_from_thread(
                        chat.write,
                        Panel(
                            Markdown(prose, code_theme="default"),
                            border_style=_BORDER,
                            padding=(1, 2),
                            title=f"[dim]step {step}[/]",
                            title_align="left",
                            subtitle=f"[dim]{out_tok} tok[/]" if out_tok else None,
                            subtitle_align="right",
                        ),
                    )

                self.agent._last_response = prose
                self.agent._history.append({
                    "role": "assistant",
                    "summary": prose[:300] if prose else "",
                    "actions": [
                        f"{a.type}:{a.path or a.pattern or ''}" for a in actions
                    ],
                })

                if not actions:
                    break

                feedback = self._exec_actions(actions, chat)
                self.agent._history.append({
                    "role": "system",
                    "results": feedback[:500],
                })

                history_block = self.agent._build_history_block()
                query = (
                    f"<original_request>{user_query}</original_request>\n\n"
                    f"{history_block}"
                    f"<action_results>\n{feedback}\n</action_results>\n\n"
                    f"The original user request is shown above. "
                    f"Continue with ONLY what was requested. "
                    f"If the task is complete, summarize what was done."
                )

        except Exception as e:
            self.call_from_thread(
                chat.write,
                Text(f"  Error: {e}", style=f"bold {_RED}"),
            )

        self.call_from_thread(self._refresh_status)

    def _exec_actions(self, actions: list, chat: RichLog) -> str:
        """Execute actions from worker thread."""
        self.call_from_thread(
            chat.write,
            Text(f"\n  — {len(actions)} action(s) —", style="dim"),
        )

        results = []
        for action in actions:
            cfg = _ACTION_STYLE.get(action.type, {
                "icon": "·", "label": "???", "confirm": True,
            })
            target = action.path or action.pattern or ""

            self.call_from_thread(
                chat.write,
                Text(f"  {cfg.get('icon', '·')} {cfg.get('label', '?')}  {target}"),
            )

            if cfg.get("confirm") and action.type == "edit_file" and action.search_text:
                preview = action.search_text.strip()[:200]
                self.call_from_thread(
                    chat.write,
                    Syntax(preview, "python", theme="default",
                           line_numbers=False, padding=(0, 1)),
                )
                if action.replace_text:
                    self.call_from_thread(
                        chat.write, Text("  → replaced by:", style="dim"),
                    )
                    rpreview = action.replace_text.strip()[:200]
                    self.call_from_thread(
                        chat.write,
                        Syntax(rpreview, "python", theme="default",
                               line_numbers=False, padding=(0, 1)),
                    )

            result = execute_action(action, self.agent.root)
            results.append(result)
            self.agent._total_actions += 1

            if result.success:
                msg = result.output.split("\n")[0]
                self.call_from_thread(
                    chat.write, Text(f"  ✓ {msg}", style=_GREEN),
                )
            else:
                self.call_from_thread(
                    chat.write, Text(f"  ✗ {result.output}", style=_RED),
                )

        parts = []
        for r in results:
            status = "SUCCESS" if r.success else ("SKIPPED" if r.skipped else "ERROR")
            parts.append(
                f'<action_result type="{r.action.type}" '
                f'path="{r.action.path or ""}" '
                f'status="{status}">\n{r.output}\n</action_result>'
            )
        return "\n\n".join(parts)

    # ──────────────────────────────────────────────────────
    # KEYBINDING ACTIONS
    # ──────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        self.agent.conversation_id = None
        self.agent._iteration = 0
        self.agent._history = []
        self.agent._attached = {}
        self.agent._attached_tokens = 0
        self.agent._repo_map = ""
        self.agent._last_response = ""
        chat = self.query_one("#chat-log", RichLog)
        chat.clear()
        chat.write(Text("  New session", style="dim"))
        self._refresh_status()

    def action_toggle_tree(self) -> None:
        tree = self.query_one("#project-tree", ProjectTree)
        self._tree_visible = not self._tree_visible
        tree.display = self._tree_visible

    def action_refresh_tree(self) -> None:
        tree = self.query_one("#project-tree", ProjectTree)
        tree.root.remove_children()
        tree._populate_node(tree.root, tree._root_path)

    def action_show_help(self) -> None:
        chat = self.query_one("#chat-log", RichLog)
        t = Table(
            show_header=True,
            header_style="bold",
            box=box.SIMPLE,
            padding=(0, 2),
        )
        t.add_column("Command / Key", style="bold", width=22)
        t.add_column("Description", style="dim")
        for cmd, desc in [
            ("/new · Ctrl+N", "New session"),
            ("/quit · Ctrl+Q", "Exit"),
            ("/tree · Ctrl+T", "Toggle tree panel"),
            ("/map", "Repo map (functions, classes)"),
            ("/attach <path>", "Pin file to context"),
            ("/detach <path>", "Unpin file"),
            ("/attached", "List pinned files"),
            ("/save [file]", "Save last response"),
            ("/stats", "Session stats"),
            ("/id", "Conversation ID"),
            ("F1", "Help"),
            ("F5", "Refresh tree"),
        ]:
            t.add_row(cmd, desc)
        chat.write(t)

    # ──────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        bar = self.query_one(TitleBar)
        bar.set_info(
            tokens=self.agent._total_tokens,
            actions=self.agent._total_actions,
            cid=self.agent.conversation_id or "",
        )

    def _show_stats(self) -> None:
        chat = self.query_one("#chat-log", RichLog)
        t = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        t.add_column("", width=18)
        t.add_column("")
        t.add_row("Conversation", self.agent.conversation_id or "(none)")
        t.add_row("Iterations", str(self.agent._iteration))
        t.add_row("Actions", str(self.agent._total_actions))
        t.add_row("Tokens", f"{self.agent._total_tokens:,}")
        t.add_row("Attached", f"{len(self.agent._attached)} files")
        chat.write(t)

    def _show_attached(self) -> None:
        chat = self.query_one("#chat-log", RichLog)
        if not self.agent._attached:
            chat.write(Text("  No files attached", style="dim"))
            return
        t = Table(box=box.SIMPLE, padding=(0, 1))
        t.add_column("File")
        t.add_column("Lines", justify="right", style="dim")
        for path, content in self.agent._attached.items():
            t.add_row(path, str(content.count("\n") + 1))
        chat.write(t)

    # ──────────────────────────────────────────────────────
    # TREE: file click → preview
    # ──────────────────────────────────────────────────────

    @on(Tree.NodeSelected, "#project-tree")
    def on_file_selected(self, event: Tree.NodeSelected) -> None:
        path = event.node.data
        if path is None or not isinstance(path, Path) or not path.is_file():
            return
        try:
            rel = path.relative_to(self.agent.root)
            content = path.read_text(encoding="utf-8")
            total = content.count("\n") + 1
            lang_map = {
                ".py": "python", ".js": "javascript", ".ts": "typescript",
                ".yaml": "yaml", ".yml": "yaml", ".json": "json",
                ".html": "html", ".css": "css", ".sh": "bash",
                ".toml": "toml", ".md": "markdown", ".sql": "sql",
                ".cfg": "ini",
            }
            lang = lang_map.get(path.suffix.lower(), "text")
            lines = content.split("\n")[:60]
            preview = "\n".join(lines)
            if total > 60:
                preview += f"\n# … {total - 60} more lines"

            chat = self.query_one("#chat-log", RichLog)
            chat.write(Text(f"\n  {rel}  ({total} lines)", style="bold"))
            chat.write(
                Syntax(preview, lang, theme="default",
                       line_numbers=True, padding=(0, 1))
            )
        except UnicodeDecodeError:
            self.query_one("#chat-log", RichLog).write(
                Text(f"  Binary file: {path.name}", style=f"dim {_AMBER}")
            )
        except Exception as e:
            self.query_one("#chat-log", RichLog).write(
                Text(f"  {e}", style=_RED)
            )


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

def run_tui(agent: CodeAgent) -> None:
    """Launch the efest(OS) TUI."""
    app = EfestOSApp(agent=agent)
    app.run()