"""
TTKIA SDK – Official Python client for TTKIA API.

Usage:
    from ttkia_sdk import TTKIAClient

    # Explicit credentials
    client = TTKIAClient(
        base_url="https://ttkia.example.com",
        api_key="ttkia_sk_..."
    )

    # Or from config file (after running: ttkia config --url ... --api-key ...)
    client = TTKIAClient()

    response = client.query("How do I configure a Fortinet VPN?")
    print(response.text)
"""

__version__ = "2.4.0"

from ttkia_sdk.client import TTKIAClient
from ttkia_sdk.models import (
    QueryResponse,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    Source,
    MCPToolResult,
    HealthStatus,
    TTKIAError,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
)

__all__ = [
    "TTKIAClient",
    "QueryResponse",
    "Conversation",
    "ConversationMessage",
    "ConversationSummary",
    "Source",
    "MCPToolResult",
    "HealthStatus",
    "TTKIAError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
]