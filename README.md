# TTKIA SDK

Official Python SDK for **TTKIA** – Telefónica Tech Knowledge Intelligence Assistant.

```
pip install ttkia-sdk
```

## Quick Start

```python
from ttkia_sdk import TTKIAClient

client = TTKIAClient(
    base_url="https://ttkia.example.com",
    bearer_token="your_app_token_here",
)

response = client.query("How do I configure a site-to-site VPN on Fortinet?")
print(response.text)
print(f"Confidence: {response.confidence:.0%}")
print(f"Sources: {len(response.sources)}")

client.close()
```

## Authentication

The SDK supports two authentication modes:

| Method | Header | Use case |
|---|---|---|
| **Bearer Token** (current) | `Authorization: Bearer <token>` | App Tokens from admin panel |
| **API Key** (future) | `X-API-Key: ttkia_pk_...` | New API key system |

```python
# App Token (current)
client = TTKIAClient(base_url="...", bearer_token="eyJhbGciOi...")

# API Key (when available)
client = TTKIAClient(base_url="...", api_key="ttkia_pk_abc123")
```

## Usage

### Context Manager (recommended)

```python
with TTKIAClient(base_url="...", bearer_token="...") as client:
    response = client.query("What is OSPF?")
    print(response.text)
```

### Conversation Continuity

```python
r1 = client.query("What is BGP?")
r2 = client.query("How does it compare to OSPF?", conversation_id=r1.conversation_id)
r3 = client.query("Which is better for my DC?", conversation_id=r1.conversation_id)
```

### Query Options

```python
response = client.query(
    "Explain SDWAN architecture",
    style="detailed",          # concise, detailed, etc.
    prompt="expert",           # prompt template
    web_search=True,           # enable web search
    teacher_mode=True,         # Chain of Thought
    sources=["sdwan.pdf"],     # filter to specific docs
    title="SDWAN Research",    # title for new conversations
)
```

### Async API

Every method has an async variant prefixed with `a`:

```python
import asyncio

async def main():
    async with TTKIAClient(base_url="...", bearer_token="...") as client:
        response = await client.aquery("What is BGP?")
        envs = await client.aget_environments()
        health = await client.ahealth()

asyncio.run(main())
```

### Batch Queries (concurrent)

```python
async def batch():
    async with TTKIAClient(base_url="...", bearer_token="...") as client:
        sem = asyncio.Semaphore(2)

        async def ask(q):
            async with sem:
                return await client.aquery(q)

        results = await asyncio.gather(
            ask("What is OSPF?"),
            ask("What is BGP?"),
            ask("What is MPLS?"),
        )
        for r in results:
            print(f"{r.query}: {r.confidence:.0%}")
```

### Feedback

```python
response = client.query("How to reset FortiGate?")
client.feedback(
    conversation_id=response.conversation_id,
    message_id=response.message_id,
    positive=True,
    comment="Accurate answer",
)
```

### Export

```python
client.export_conversation(response.conversation_id, "session.zip")
```

## Error Handling

```python
from ttkia_sdk import TTKIAError, AuthenticationError, RateLimitError
import time

try:
    response = client.query("...")
except AuthenticationError:
    print("Token invalid or expired")
except RateLimitError as e:
    time.sleep(e.retry_after)
except TTKIAError as e:
    print(f"Error [{e.status_code}]: {e.message}")
```

## Response Object

```python
response = client.query("What is OSPF?")

response.text                  # The answer
response.confidence            # 0.0 – 1.0
response.conversation_id       # For follow-ups
response.message_id            # For feedback
response.is_error              # True if failed

response.sources               # All sources (docs + webs)
response.docs                  # Document sources
response.webs                  # Web sources

response.token_usage.input_tokens
response.token_usage.output_tokens
response.token_usage.total

response.timing.total_seconds
response.timing.get_step("retrieve")
response.timing.summary()      # {"retrieve": 0.5, "textual": 2.1, ...}

response.thinking_process      # CoT steps (when teacher_mode=True)
```

## Available Methods

| Method | Async | Description |
|---|---|---|
| `query()` | `aquery()` | Send query via /query_complete |
| `health()` | `ahealth()` | Check service health |
| `get_environments()` | `aget_environments()` | List available environments |
| `get_prompts()` | `aget_prompts()` | List prompt templates |
| `get_styles()` | `aget_styles()` | List response styles |
| `list_conversations()` | `alist_conversations()` | List user conversations |
| `get_conversation()` | `aget_conversation()` | Get conversation with messages |
| `create_conversation()` | `acreate_conversation()` | Create new conversation |
| `delete_conversation()` | `adelete_conversation()` | Delete a conversation |
| `feedback()` | `afeedback()` | Submit response feedback |
| `export_conversation()` | `aexport_conversation()` | Export as ZIP |

## Development

```bash
git clone https://github.com/xerar/TTKIA_SDK.git
cd TTKIA_SDK
pip install -e ".[dev]"
pytest tests/ -v
```

## Requirements

- Python ≥ 3.9
- httpx ≥ 0.25
- pydantic ≥ 2.0

## License

MIT
