"""
TTKIA CLI – Command line interface for TTKIA.

Usage:
    ttkia ask "How do I configure OSPF?"
    ttkia chat
    ttkia health
    ttkia envs
    ttkia history
    ttkia config --url https://ttkia.example.com --token eyJhbG...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from ttkia_sdk import TTKIAClient, TTKIAError, AuthenticationError, RateLimitError


# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

_CONFIG_DIR = Path.home() / ".ttkia"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        return json.loads(_CONFIG_FILE.read_text())
    return {}


def _save_config(config: dict):
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))
    _CONFIG_FILE.chmod(0o600)  # Owner-only read/write


def _get_client() -> TTKIAClient:
    """Build a TTKIAClient from config file or environment variables."""
    config = _load_config()

    url = os.environ.get("TTKIA_URL") or config.get("url")
    token = os.environ.get("TTKIA_TOKEN") or config.get("token")
    api_key = os.environ.get("TTKIA_API_KEY") or config.get("api_key")

    if not url:
        print("❌ No TTKIA URL configured.")
        print("   Run: ttkia config --url https://your-ttkia-server.com --token YOUR_TOKEN")
        print("   Or set: export TTKIA_URL=... and export TTKIA_TOKEN=...")
        sys.exit(1)

    if not token and not api_key:
        print("❌ No authentication configured.")
        print("   Run: ttkia config --url URL --token YOUR_APP_TOKEN")
        sys.exit(1)

    verify_ssl = config.get("verify_ssl", True)
    timeout = config.get("timeout", 120)

    return TTKIAClient(
        base_url=url,
        bearer_token=token,
        api_key=api_key,
        timeout=timeout,
        verify_ssl=verify_ssl,
    )


# ═══════════════════════════════════════════════════════════
# COLORS (ANSI)
# ═══════════════════════════════════════════════════════════

class _C:
    """ANSI color codes – disabled if not a TTY."""
    _enabled = sys.stdout.isatty()

    BOLD = "\033[1m" if _enabled else ""
    DIM = "\033[2m" if _enabled else ""
    GREEN = "\033[32m" if _enabled else ""
    YELLOW = "\033[33m" if _enabled else ""
    CYAN = "\033[36m" if _enabled else ""
    RED = "\033[31m" if _enabled else ""
    MAGENTA = "\033[35m" if _enabled else ""
    RESET = "\033[0m" if _enabled else ""


# ═══════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════

def cmd_config(args):
    """Configure TTKIA connection."""
    config = _load_config()

    if args.url:
        config["url"] = args.url.rstrip("/")
    if args.token:
        config["token"] = args.token
    if args.api_key:
        config["api_key"] = args.api_key
    if args.timeout:
        config["timeout"] = args.timeout
    if args.no_ssl:
        config["verify_ssl"] = False
    if args.ssl:
        config["verify_ssl"] = True

    if args.url or args.token or args.api_key:
        _save_config(config)
        print(f"✅ Config saved to {_CONFIG_FILE}")
    
    # Show current config
    config = _load_config()
    if config:
        print(f"\n{_C.BOLD}Current configuration:{_C.RESET}")
        print(f"  URL:     {config.get('url', '(not set)')}")
        token_val = config.get('token', '')
        if token_val:
            print(f"  Token:   {token_val[:20]}...{token_val[-8:]}")
        else:
            print(f"  Token:   (not set)")
        api_key_val = config.get('api_key', '')
        if api_key_val:
            print(f"  API Key: {api_key_val[:16]}...")
        print(f"  Timeout: {config.get('timeout', 120)}s")
        print(f"  SSL:     {config.get('verify_ssl', True)}")
    else:
        print("No configuration found.")


def cmd_health(args):
    """Check TTKIA service health."""
    with _get_client() as client:
        try:
            h = client.health()
            status_icon = "🟢" if h.is_healthy else "🔴"
            print(f"{status_icon} TTKIA: {h.status}")
        except TTKIAError as e:
            print(f"🔴 Connection failed: {e}")
            sys.exit(1)


def cmd_ask(args):
    """Send a single query and display the response."""
    query = " ".join(args.query)
    if not query:
        print("❌ Provide a query: ttkia ask \"your question here\"")
        sys.exit(1)

    with _get_client() as client:
        try:
            t0 = time.time()
            response = client.query(
                query,
                conversation_id=args.conversation,
                style=args.style,
                prompt=args.prompt,
                web_search=args.web,
                teacher_mode=args.cot,
            )
            elapsed = time.time() - t0

            if response.is_error:
                print(f"{_C.RED}❌ Error: {response.error}{_C.RESET}")
                sys.exit(1)

            # Response text
            print(f"\n{response.text}\n")

            # Metadata footer
            conf = response.confidence or 0
            conf_color = _C.GREEN if conf >= 0.7 else _C.YELLOW if conf >= 0.4 else _C.RED
            
            print(f"{_C.DIM}{'─' * 60}")
            print(f"  Confidence: {conf_color}{conf:.0%}{_C.DIM}"
                  f"  │  Sources: {len(response.docs)}d/{len(response.webs)}w"
                  f"  │  Tokens: {response.token_usage.total}"
                  f"  │  Time: {elapsed:.1f}s")
            print(f"  Conversation: {response.conversation_id}{_C.RESET}")

            # Sources
            if args.sources and response.sources:
                print(f"\n{_C.DIM}  Sources:{_C.RESET}")
                for s in response.sources:
                    icon = "🌐" if s.is_web else "📄"
                    print(f"    {icon} {s.title or s.source}")

            # Thinking process
            if args.cot and response.thinking_process:
                print(f"\n{_C.DIM}  Thinking:{_C.RESET}")
                for step in response.thinking_process:
                    print(f"    💭 {step[:120]}")

            # JSON output
            if args.json:
                print(f"\n{_C.DIM}─── JSON ───{_C.RESET}")
                out = {
                    "query": response.query,
                    "text": response.text,
                    "confidence": response.confidence,
                    "conversation_id": response.conversation_id,
                    "message_id": response.message_id,
                    "tokens": {"input": response.token_usage.input_tokens, "output": response.token_usage.output_tokens},
                    "timing": response.timing.summary(),
                    "sources": [{"title": s.title, "source": s.source, "web": s.is_web} for s in response.sources],
                }
                print(json.dumps(out, indent=2, ensure_ascii=False))

        except AuthenticationError:
            print(f"{_C.RED}❌ Authentication failed. Check your token with: ttkia config{_C.RESET}")
            sys.exit(1)
        except RateLimitError as e:
            print(f"{_C.YELLOW}⏳ Rate limited. Retry in {e.retry_after}s{_C.RESET}")
            sys.exit(1)
        except TTKIAError as e:
            print(f"{_C.RED}❌ Error [{e.status_code}]: {e.message}{_C.RESET}")
            sys.exit(1)


def cmd_chat(args):
    """Interactive chat session."""
    client = _get_client()
    conversation_id = args.conversation

    print(f"{_C.BOLD}💬 TTKIA Interactive Chat{_C.RESET}")
    print(f"{_C.DIM}   Style: {args.style} │ Prompt: {args.prompt}")
    print(f"   Commands: /quit  /new  /export  /sources  /id{_C.RESET}")
    print()

    try:
        while True:
            try:
                user_input = input(f"{_C.CYAN}You:{_C.RESET} ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                cmd = user_input.lower().split()[0]
                if cmd in ("/quit", "/exit", "/q"):
                    break
                elif cmd == "/new":
                    conversation_id = None
                    print(f"{_C.DIM}  ↻ New conversation{_C.RESET}")
                    continue
                elif cmd == "/export":
                    if conversation_id:
                        path = f"ttkia_chat_{conversation_id[:8]}.zip"
                        client.export_conversation(conversation_id, path)
                        print(f"{_C.DIM}  📦 Exported to {path}{_C.RESET}")
                    else:
                        print(f"{_C.DIM}  No active conversation{_C.RESET}")
                    continue
                elif cmd == "/sources":
                    args._show_sources = not getattr(args, '_show_sources', False)
                    state = "on" if args._show_sources else "off"
                    print(f"{_C.DIM}  Sources: {state}{_C.RESET}")
                    continue
                elif cmd == "/id":
                    print(f"{_C.DIM}  Conversation: {conversation_id or '(none)'}{_C.RESET}")
                    continue
                elif cmd == "/web":
                    args._web = not getattr(args, '_web', False)
                    state = "on" if args._web else "off"
                    print(f"{_C.DIM}  Web search: {state}{_C.RESET}")
                    continue
                elif cmd == "/help":
                    print(f"{_C.DIM}  /quit    Exit chat")
                    print(f"  /new     Start new conversation")
                    print(f"  /export  Export conversation as ZIP")
                    print(f"  /sources Toggle source display")
                    print(f"  /web     Toggle web search")
                    print(f"  /id      Show conversation ID{_C.RESET}")
                    continue
                else:
                    print(f"{_C.DIM}  Unknown command. Type /help{_C.RESET}")
                    continue

            # Query
            try:
                t0 = time.time()
                response = client.query(
                    user_input,
                    conversation_id=conversation_id,
                    style=args.style,
                    prompt=args.prompt,
                    web_search=getattr(args, '_web', False),
                )
                elapsed = time.time() - t0

                if response.is_error:
                    print(f"\n{_C.RED}❌ {response.error}{_C.RESET}\n")
                    continue

                # Update conversation
                if not conversation_id:
                    conversation_id = response.conversation_id

                # Print response
                conf = response.confidence or 0
                conf_color = _C.GREEN if conf >= 0.7 else _C.YELLOW if conf >= 0.4 else _C.RED

                print(f"\n{_C.BOLD}TTKIA:{_C.RESET} {response.text}")
                print(f"{_C.DIM}  [{conf_color}{conf:.0%}{_C.DIM} │ {len(response.sources)}src │ {response.token_usage.total}tok │ {elapsed:.1f}s]{_C.RESET}\n")

                # Sources
                if getattr(args, '_show_sources', False) and response.sources:
                    for s in response.sources:
                        icon = "🌐" if s.is_web else "📄"
                        print(f"  {_C.DIM}{icon} {s.title or s.source}{_C.RESET}")
                    print()

            except RateLimitError as e:
                print(f"\n{_C.YELLOW}⏳ Rate limited. Wait {e.retry_after}s{_C.RESET}\n")
                time.sleep(e.retry_after)
            except TTKIAError as e:
                print(f"\n{_C.RED}❌ [{e.status_code}] {e.message}{_C.RESET}\n")

    finally:
        client.close()
        if conversation_id:
            print(f"\n{_C.DIM}Conversation: {conversation_id}{_C.RESET}")
        print(f"{_C.DIM}👋 Bye{_C.RESET}")


def cmd_envs(args):
    """List available environments."""
    with _get_client() as client:
        envs = client.get_environments()
        print(f"{_C.BOLD}Environments ({len(envs)}):{_C.RESET}")
        for env in envs:
            print(f"  🏢 {env}")


def cmd_prompts(args):
    """List available prompts."""
    with _get_client() as client:
        prompts = client.get_prompts()
        print(f"{_C.BOLD}Prompts ({len(prompts)}):{_C.RESET}")
        for p in prompts:
            pid = p.get("id", p.get("name", "?"))
            desc = p.get("description", "")
            print(f"  📝 {_C.CYAN}{pid}{_C.RESET}: {desc}")


def cmd_styles(args):
    """List available response styles."""
    with _get_client() as client:
        styles = client.get_styles()
        print(f"{_C.BOLD}Styles ({len(styles)}):{_C.RESET}")
        for s in styles:
            sid = s.get("id", s.get("name", "?"))
            desc = s.get("description", "")
            print(f"  🎨 {_C.CYAN}{sid}{_C.RESET}: {desc}")


def cmd_history(args):
    """List recent conversations."""
    with _get_client() as client:
        convs = client.list_conversations()
        if not convs:
            print("No conversations found.")
            return

        print(f"{_C.BOLD}Conversations ({len(convs)}):{_C.RESET}")
        for c in convs[:args.limit]:
            title = c.title or "(untitled)"
            cid = c.conversation_id[:8]
            date = (c.updated_at or c.created_at or "")[:10]
            print(f"  💬 {_C.DIM}{cid}{_C.RESET} {title} {_C.DIM}{date}{_C.RESET}")


def cmd_export(args):
    """Export a conversation."""
    with _get_client() as client:
        output = args.output or f"conversation_{args.id[:8]}.zip"
        client.export_conversation(args.id, output)
        print(f"📦 Exported to {output}")


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="ttkia",
        description="TTKIA CLI – Interact with TTKIA from the command line",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── config ──
    p = sub.add_parser("config", help="Configure TTKIA connection")
    p.add_argument("--url", help="TTKIA server URL")
    p.add_argument("--token", help="App Token (Bearer)")
    p.add_argument("--api-key", help="API Key (ttkia_sk_...)")
    p.add_argument("--timeout", type=int, help="Request timeout in seconds")
    p.add_argument("--no-ssl", action="store_true", help="Disable SSL verification")
    p.add_argument("--ssl", action="store_true", help="Enable SSL verification")
    p.set_defaults(func=cmd_config)

    # ── health ──
    p = sub.add_parser("health", help="Check service health")
    p.set_defaults(func=cmd_health)

    # ── ask ──
    p = sub.add_parser("ask", help="Send a query")
    p.add_argument("query", nargs="+", help="The question to ask")
    p.add_argument("-c", "--conversation", help="Continue a conversation (ID)")
    p.add_argument("-s", "--style", default="concise", help="Response style (default: concise)")
    p.add_argument("-p", "--prompt", default="default", help="Prompt template (default: default)")
    p.add_argument("--web", action="store_true", help="Enable web search")
    p.add_argument("--cot", action="store_true", help="Enable Chain of Thought")
    p.add_argument("--sources", action="store_true", help="Show source documents")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=cmd_ask)

    # ── chat ──
    p = sub.add_parser("chat", help="Interactive chat session")
    p.add_argument("-c", "--conversation", help="Continue a conversation (ID)")
    p.add_argument("-s", "--style", default="concise", help="Response style")
    p.add_argument("-p", "--prompt", default="default", help="Prompt template")
    p.set_defaults(func=cmd_chat)

    # ── envs ──
    p = sub.add_parser("envs", help="List environments")
    p.set_defaults(func=cmd_envs)

    # ── prompts ──
    p = sub.add_parser("prompts", help="List prompt templates")
    p.set_defaults(func=cmd_prompts)

    # ── styles ──
    p = sub.add_parser("styles", help="List response styles")
    p.set_defaults(func=cmd_styles)

    # ── history ──
    p = sub.add_parser("history", help="List conversations")
    p.add_argument("-n", "--limit", type=int, default=20, help="Max items (default: 20)")
    p.set_defaults(func=cmd_history)

    # ── export ──
    p = sub.add_parser("export", help="Export a conversation")
    p.add_argument("id", help="Conversation ID")
    p.add_argument("-o", "--output", help="Output filename")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
