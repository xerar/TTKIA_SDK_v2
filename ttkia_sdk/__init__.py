"""
TTKIA SDK – Official Python client for TTKIA API.

Usage:
    from ttkia_sdk import TTKIAClient

    client = TTKIAClient(
        base_url="https://ttkia.example.com",
        api_key="ttkia_pk_..."
    )

    response = client.query("How do I configure a Fortinet VPN?")
    print(response.text)
"""

__version__ = "2.1.0"

from ttkia_sdk.client import TTKIAClient
from ttkia_sdk.models import (
    QueryResponse,
    Conversation,
    ConversationMessage,
    Source,
    HealthStatus,
    TTKIAError,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
    StreamEvent, 
)

__all__ = [
    "TTKIAClient",
    "QueryResponse",
    "Conversation",
    "ConversationMessage",
    "Source",
    "HealthStatus",
    "TTKIAError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "StreamEvent", 
]
