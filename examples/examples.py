"""
TTKIA SDK – Usage Examples
===========================

All examples use the /query_complete REST endpoint.
Replace base_url and api_key with your actual values.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os

from ttkia_sdk import (
    TTKIAClient,
    TTKIAError,
    AuthenticationError,
    RateLimitError,
)


# ══════════════════════════════════════════════════════════
# CONFIGURATION – Change these values
# ══════════════════════════════════════════════════════════
# Carga variables desde .env
load_dotenv()

BASE_URL = os.getenv("TTKIA_BASE_URL")
API_KEY = os.getenv("TTKIA_API_KEY")

if not BASE_URL or not API_KEY:
    raise RuntimeError(
        "Missing TTKIA_BASE_URL or TTKIA_API_KEY. "
        "Define them in .env or environment variables."
    )

# ─────────────────────────────────────────────────────────
# 1. SIMPLE QUERY
# ─────────────────────────────────────────────────────────

def example_simple_query():
    """The simplest possible query."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

        response = client.query("How do I configure a site-to-site VPN on Fortinet?")

        print(f"✅ Answer: {response.text[:300]}...")
        print(f"📊 Confidence: {response.confidence:.0%}")
        print(f"📚 Sources: {len(response.sources)} ({len(response.docs)} docs, {len(response.webs)} web)")
        print(f"💰 Tokens: {response.token_usage.total} ({response.token_usage.input_tokens} in / {response.token_usage.output_tokens} out)")
        print(f"⏱️  Time: {response.timing.total_seconds:.1f}s")
        print(f"🔗 Conversation: {response.conversation_id}")


# ─────────────────────────────────────────────────────────
# 2. CONVERSATION CONTINUITY
# ─────────────────────────────────────────────────────────

def example_conversation():
    """Multi-turn conversation keeping context."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

        # First question
        r1 = client.query("What is OSPF?", title="OSPF Learning Session")
        print(f"Q1: {r1.text[:150]}...")
        print(f"    Conversation: {r1.conversation_id}\n")

        # Follow-up in same conversation
        r2 = client.query(
            "How does it compare to BGP?",
            conversation_id=r1.conversation_id,
        )
        print(f"Q2: {r2.text[:150]}...")

        # Another follow-up
        r3 = client.query(
            "Which should I use for my data center?",
            conversation_id=r1.conversation_id,
        )
        print(f"Q3: {r3.text[:150]}...")


# ─────────────────────────────────────────────────────────
# 3. CHAIN OF THOUGHT (Teacher Mode)
# ─────────────────────────────────────────────────────────

def example_chain_of_thought():
    """Enable CoT for deeper reasoning."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

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
    """Query with real-time web search enabled."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

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
    """Proper error handling with retry for production code."""

    MAX_RETRIES = 3

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY, timeout=180.0) as client:

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
# 6. BATCH QUERIES (async, controlled concurrency)
# ─────────────────────────────────────────────────────────

async def example_batch_queries():
    """
    Process multiple queries sequentially.
    
    Sequential execution is recommended because the TTKIA backend
    processes queries through a full RAG pipeline (retrieve → generate → analyze)
    which is resource-intensive. Concurrent requests can cause auth conflicts
    and degrade response quality due to CPU/memory contention.
    """

    queries = [
        "What is the default MTU for VXLAN?",
        "How to configure SNMP v3 on Fortinet?",
        "Explain Zero Trust Network Access",
        "What are the CheckPoint Gaia CLI commands for NAT?",
        "How to troubleshoot SDWAN overlay flapping?",
    ]

    async with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

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
                    "time": response.timing.total_seconds,
                })
                print(f"✅ {response.confidence:.0%} ({response.timing.total_seconds:.1f}s)")
            except TTKIAError as e:
                results.append({
                    "query": q, "answer": f"ERROR: {e.message}",
                    "confidence": 0, "sources": 0, "tokens": 0, "time": 0,
                })
                print(f"❌ {e.message}")

        print("\n" + "=" * 70)
        print("BATCH RESULTS")
        print("=" * 70)

        total_tokens = 0
        for r in results:
            conf = r["confidence"] or 0
            total_tokens += r["tokens"]
            print(f"\n📌 {r['query']}")
            print(f"   Confidence: {conf:.0%} | Sources: {r['sources']} | Tokens: {r['tokens']} | Time: {r['time']:.1f}s")
            print(f"   Answer: {r['answer']}...")

        print(f"\n💰 Total tokens used: {total_tokens}")


# ─────────────────────────────────────────────────────────
# 7. AUTOMATED INCIDENT ANALYSIS
# ─────────────────────────────────────────────────────────

def example_incident_analysis():
    """Analyze an incident – could be triggered by monitoring alert."""

    alert = {
        "device": "FW-CORE-01",
        "vendor": "Fortinet",
        "model": "FortiGate 600E",
        "issue": "High CPU at 95% for 15 minutes",
        "interfaces": ["port1", "port2", "wan1"],
    }

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

        query = (
            f"Incident analysis for {alert['device']} ({alert['vendor']} {alert['model']}): "
            f"{alert['issue']}. Affected interfaces: {', '.join(alert['interfaces'])}. "
            f"Provide: 1) Probable root causes 2) Diagnostic commands "
            f"3) Immediate mitigation 4) Prevention recommendations"
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
        print(f"📊 Confidence: {response.confidence:.0%}")
        print(f"📚 Sources: {len(response.sources)}")
        print(f"💰 Tokens: {response.token_usage.total}")

        # Export full conversation for audit trail
        client.export_conversation(
            response.conversation_id,
            f"incident_{alert['device']}_{datetime.now():%Y%m%d_%H%M}.zip"
        )
        print("📦 Conversation exported")


# ─────────────────────────────────────────────────────────
# 8. FEEDBACK LOOP
# ─────────────────────────────────────────────────────────

def example_feedback():
    """Submit feedback to improve TTKIA responses."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

        response = client.query("How do I enable MFA on CheckPoint Gaia?")
        print(f"Answer: {response.text[:200]}...")

        result = client.feedback(
            conversation_id=response.conversation_id,
            message_id=response.message_id,
            positive=True,
            comment="Accurate and well-structured answer",
        )
        print(f"✅ Feedback: {result.message}")


# ─────────────────────────────────────────────────────────
# 9. EXPLORE ENVIRONMENTS & CAPABILITIES
# ─────────────────────────────────────────────────────────

def example_explore():
    """Discover available environments, prompts and styles."""

    with TTKIAClient(base_url=BASE_URL, api_key=API_KEY) as client:

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


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("TTKIA SDK Examples")
    print("=" * 70)

    # ── Sync examples ──
    example_simple_query()
    # example_conversation()
    # example_chain_of_thought()
    # example_web_search()
    # example_error_handling()
    # example_incident_analysis()
    # example_feedback()
    # example_explore()

    # ── Async example ──
    # asyncio.run(example_batch_queries())
