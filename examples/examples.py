"""
TTKIA SDK – Usage Examples
===========================

Configuration priority (handled by TTKIAClient automatically):
  1. Environment variables: TTKIA_URL, TTKIA_API_KEY (or TTKIA_TOKEN)
  2. .env file in this directory (if python-dotenv is installed)
  3. ~/.ttkia/config.json (created by: ttkia config --url ... --api-key ...)

Optional:
  - TTKIA_EXAMPLE = simple | conv | cot | web | errors | batch | incident | feedback | explore
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from ttkia_sdk import (
    TTKIAClient,
    TTKIAError,
    AuthenticationError,
    RateLimitError,
)

# ─────────────────────────────────────────────────────────
# OPTIONAL .env SUPPORT
# ─────────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent / ".env"

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(dotenv_path=env_path)
except ModuleNotFoundError:
    pass

EXAMPLE = (os.getenv("TTKIA_EXAMPLE") or "simple").strip().lower()


def _client(**kwargs) -> TTKIAClient:
    """Create a TTKIAClient using auto-config resolution.

    Priority: explicit kwargs > env vars > ~/.ttkia/config.json
    If a .env file exists in examples/, its vars are already loaded above.
    """
    return TTKIAClient(**kwargs)


# ─────────────────────────────────────────────────────────
# 1. SIMPLE QUERY
# ─────────────────────────────────────────────────────────
def example_simple_query():
    with _client() as client:
        response = client.query("How do I configure a site-to-site VPN on Fortinet?")
        print(f"✅ Answer: {response.text[:300]}...")
        print(f"📊 Confidence: {response.confidence:.0%}")
        print(f"📚 Sources: {len(response.sources)} ({len(response.docs)} docs, {len(response.webs)} web)")
        print(f"💰 Tokens: {response.token_usage.total} ({response.token_usage.input_tokens} in / {response.token_usage.output_tokens} out)")
        print(f"⏱️  Time: {response.timing.total:.1f}s")
        print(f"🔗 Conversation: {response.conversation_id}")
        if response.used_mcp:
            print(f"🔧 MCP Tools: {len(response.mcp_tools)} used")
            for t in response.mcp_tools:
                print(f"   {'✅' if t.is_success else '❌'} {t.name}")


# ─────────────────────────────────────────────────────────
# 2. CONVERSATION CONTINUITY
# ─────────────────────────────────────────────────────────
def example_conversation():
    with _client() as client:
        r1 = client.query("What is OSPF?", title="OSPF Learning Session")
        print(f"Q1: {r1.text[:150]}...")
        print(f"    Conversation: {r1.conversation_id}\n")

        r2 = client.query("How does it compare to BGP?", conversation_id=r1.conversation_id)
        print(f"Q2: {r2.text[:150]}...")

        r3 = client.query("Which should I use for my data center?", conversation_id=r1.conversation_id)
        print(f"Q3: {r3.text[:150]}...")


# ─────────────────────────────────────────────────────────
# 3. CHAIN OF THOUGHT (Teacher Mode)
# ─────────────────────────────────────────────────────────
def example_chain_of_thought():
    with _client() as client:
        response = client.query(
            "Compare VXLAN vs VLAN for a multi-tenant data center with 5000 tenants",
            teacher_mode=True,
            style="detailed",
        )
        print(f"Answer: {response.text[:300]}...")
        if response.thinking_process:
            print(f"\n🧠 Thinking steps: {len(response.thinking_process)}")
            for i, step in enumerate(response.thinking_process[:3], 1):
                print(f"   Step {i}: {step[:100]}...")


# ─────────────────────────────────────────────────────────
# 4. WEB SEARCH AUGMENTED
# ─────────────────────────────────────────────────────────
def example_web_search():
    with _client() as client:
        response = client.query(
            "What are the latest CVEs for Cisco IOS XE in 2025?",
            web_search=True,
            style="detailed",
        )
        print(f"Answer: {response.text[:300]}...")
        print(f"\n🌐 Web sources: {len(response.webs)}")
        for web in response.webs:
            print(f"   • {web.title}: {web.source}")


# ─────────────────────────────────────────────────────────
# 5. ERROR HANDLING (production pattern)
# ─────────────────────────────────────────────────────────
def example_error_handling():
    MAX_RETRIES = 3

    with _client(timeout=180.0) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.query("Diagnose high CPU on Palo Alto PA-5200")

                if response.is_error:
                    print(f"⚠️ Attempt {attempt}: Query error: {response.error}")
                    if attempt < MAX_RETRIES:
                        time.sleep(2 * attempt)
                        continue
                    break

                print(f"✅ Success on attempt {attempt}")
                print(f"Answer: {response.text[:200]}...")
                return response

            except AuthenticationError:
                print("❌ Token invalid or expired. Request a new one.")
                return None

            except RateLimitError as e:
                print(f"⏳ Rate limited. Waiting {e.retry_after}s (attempt {attempt})")
                time.sleep(e.retry_after)

            except TTKIAError as e:
                print(f"💥 Error [{e.status_code}]: {e.message}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 * attempt)
                else:
                    raise

        print("❌ All retries exhausted")
        return None


# ─────────────────────────────────────────────────────────
# 6. BATCH QUERIES (async)
# ─────────────────────────────────────────────────────────
async def example_batch_queries():
    queries = [
        "What is the default MTU for VXLAN?",
        "How to configure SNMP v3 on Fortinet?",
        "Explain Zero Trust Network Access",
        "What are the CheckPoint Gaia CLI commands for NAT?",
        "How to troubleshoot SDWAN overlay flapping?",
    ]

    async with _client() as client:
        results = []
        print("🚀 Starting batch queries...\n")

        for i, q in enumerate(queries, 1):
            print(f"  [{i}/{len(queries)}] {q[:60]}...", end=" ", flush=True)
            try:
                response = await client.aquery(q)
                results.append({
                    "query": q,
                    "answer": response.text[:200],
                    "confidence": response.confidence,
                    "sources": len(response.sources),
                    "tokens": response.token_usage.total,
                    "time": response.timing.total,
                })
                print(f"✅ {response.confidence:.0%} ({response.timing.total:.1f}s)")
            except TTKIAError as e:
                results.append({
                    "query": q,
                    "answer": f"ERROR: {e.message}",
                    "confidence": 0,
                    "sources": 0,
                    "tokens": 0,
                    "time": 0,
                })
                print(f"❌ {e.message}")

        total_tokens = sum(r["tokens"] for r in results)
        print(f"\n💰 Total tokens used: {total_tokens}")


# ─────────────────────────────────────────────────────────
# 7. AUTOMATED INCIDENT ANALYSIS
# ─────────────────────────────────────────────────────────
def example_incident_analysis():
    alert = {
        "device": "FW-CORE-01",
        "vendor": "Fortinet",
        "model": "FortiGate 600E",
        "issue": "High CPU at 95% for 15 minutes",
        "interfaces": ["port1", "port2", "wan1"],
    }

    with _client() as client:
        query = (
            f"Incident analysis for {alert['device']} ({alert['vendor']} {alert['model']}): "
            f"{alert['issue']}. Affected interfaces: {', '.join(alert['interfaces'])}. "
            "Provide: 1) Probable root causes 2) Diagnostic commands "
            "3) Immediate mitigation 4) Prevention recommendations"
        )

        response = client.query(query, style="detailed", title="Auto-Incident Analysis")

        report = {
            "timestamp": datetime.now().isoformat(),
            "alert": alert,
            "analysis": response.text,
            "confidence": response.confidence,
            "sources_used": len(response.sources),
            "conversation_id": response.conversation_id,
            "tokens": response.token_usage.total,
        }

        output = Path("incident_report.json")
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"📋 Report saved to {output}")

        client.export_conversation(
            response.conversation_id,
            f"incident_{alert['device']}_{datetime.now():%Y%m%d_%H%M}.zip"
        )
        print("📦 Conversation exported")


# ─────────────────────────────────────────────────────────
# 8. FEEDBACK LOOP
# ─────────────────────────────────────────────────────────
def example_feedback():
    with _client() as client:
        response = client.query("How do I enable MFA on CheckPoint Gaia?")
        print(f"Answer: {response.text[:200]}...")

        result = client.send_feedback(
            conversation_id=response.conversation_id,
            message_id=response.message_id,
            score=1,
        )
        print(f"✅ Feedback: {result.message}")


# ─────────────────────────────────────────────────────────
# 9. EXPLORE ENVIRONMENTS & CAPABILITIES
# ─────────────────────────────────────────────────────────
def example_explore():
    with _client() as client:
        print("🏢 Environments:")
        for env in client.get_environments():
            print(f"   • {env}")

        print("\n📝 Prompts:")
        for p in client.get_prompts():
            name = p.get("id", p.get("name", "?"))
            desc = p.get("description", "")[:60]
            print(f"   • {name}: {desc}")

        print("\n🎨 Styles:")
        for s in client.get_styles():
            name = s.get("id", s.get("name", "?"))
            print(f"   • {name}")

        convs = client.list_conversations()
        print(f"\n💬 Conversations: {len(convs)}")
        for c in convs[:5]:
            print(f"   • {c.conversation_id[:8]} {c.title or '(untitled)'}")


# ─────────────────────────────────────────────────────────
# MAIN (select via TTKIA_EXAMPLE)
# ─────────────────────────────────────────────────────────
EXAMPLES = {
    "simple": example_simple_query,
    "conv": example_conversation,
    "cot": example_chain_of_thought,
    "web": example_web_search,
    "errors": example_error_handling,
    "incident": example_incident_analysis,
    "feedback": example_feedback,
    "explore": example_explore,
}

if __name__ == "__main__":
    print("=" * 70)
    print("TTKIA SDK Examples")
    print("=" * 70)
    print(f"Using example: {EXAMPLE}")

    if EXAMPLE == "batch":
        asyncio.run(example_batch_queries())
    else:
        fn = EXAMPLES.get(EXAMPLE)
        if not fn:
            valid = ", ".join(list(EXAMPLES.keys()) + ["batch"])
            raise SystemExit(f"Unknown TTKIA_EXAMPLE={EXAMPLE!r}. Valid: {valid}")
        fn()