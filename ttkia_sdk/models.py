"""
TTKIA SDK – Data models.

All Pydantic models that represent API responses and data structures.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════

class TTKIAError(Exception):
    """Base exception for all TTKIA SDK errors."""

    def __init__(self, message: str, status_code: int = 0):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthenticationError(TTKIAError):
    """Raised when authentication fails (401)."""
    pass


class RateLimitError(TTKIAError):
    """Raised when rate-limited (429)."""

    def __init__(self, message: str, retry_after: int = 60, status_code: int = 429):
        self.retry_after = retry_after
        super().__init__(message, status_code)


class NotFoundError(TTKIAError):
    """Raised when a resource is not found (404)."""
    pass


class InsufficientScopeError(TTKIAError):
    """Raised when API Key lacks required scope (403)."""
    pass


# ═══════════════════════════════════════════════════════════
# VALUE OBJECTS
# ═══════════════════════════════════════════════════════════

class Source(BaseModel):
    """A source document or web result referenced in the response."""
    title: str = ""
    source: str = ""
    environment: str = ""
    page: Optional[int] = None
    relevance: Optional[float] = None

    @property
    def is_web(self) -> bool:
        s = self.source.lower()
        return s.startswith("http://") or s.startswith("https://")

    def __str__(self) -> str:
        return self.title or self.source or "(unknown)"


class TokenUsage(BaseModel):
    """Token usage statistics for a query."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return f"{self.input_tokens}in/{self.output_tokens}out ({self.total} total)"


class TimingInfo(BaseModel):
    """Pipeline timing breakdown."""
    raw: List[Dict[str, Any]] = Field(default_factory=list)

    def get(self, phase: str) -> Optional[float]:
        for entry in self.raw:
            if phase in entry:
                return entry[phase]
        return None

    @property
    def total(self) -> float:
        return sum(v for d in self.raw for v in d.values() if isinstance(v, (int, float)))

    def summary(self) -> Dict[str, float]:
        out = {}
        for d in self.raw:
            out.update(d)
        out["total"] = self.total
        return out

    def __str__(self) -> str:
        parts = [f"{k}={v:.2f}s" for d in self.raw for k, v in d.items()]
        return f"({', '.join(parts)}) total={self.total:.2f}s"


class MCPToolResult(BaseModel):
    """Result from an MCP tool execution."""
    name: str = ""
    status: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    def __str__(self) -> str:
        if self.is_success:
            preview = str(self.result)[:100]
            return f"✅ {self.name}: {preview}"
        return f"❌ {self.name}: {self.status}"


# ═══════════════════════════════════════════════════════════
# QUERY RESPONSE
# ═══════════════════════════════════════════════════════════

class QueryResponse(BaseModel):
    """
    Full response from a /query_complete request.

    Maps directly to QueryCompleteResponse in the backend.
    """
    success: bool
    conversation_id: str = ""
    message_id: str = ""
    query: str = ""
    text: str = Field("", alias="response_text")
    confidence: Optional[float] = None
    recommended_response: Optional[str] = None
    query_extended: Optional[str] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    timing: TimingInfo = Field(default_factory=TimingInfo)
    inferred_environments: List[str] = Field(default_factory=list)
    docs: List[Source] = Field(default_factory=list)
    webs: List[Source] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    thinking_process: List[str] = Field(default_factory=list)
    mcp_tools: List[MCPToolResult] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    model_config = {"populate_by_name": True}

    @property
    def is_error(self) -> bool:
        return not self.success or self.error is not None

    @property
    def sources(self) -> List[Source]:
        """All sources (docs + webs) combined."""
        return self.docs + self.webs

    @property
    def used_mcp(self) -> bool:
        """True if MCP tools were used in this response."""
        return len(self.mcp_tools) > 0

    def __str__(self) -> str:
        if self.is_error:
            return f"[ERROR] {self.error}"
        preview = self.text[:200] + "..." if len(self.text) > 200 else self.text
        return f"[{self.confidence or 0:.0%}] {preview}"


# ═══════════════════════════════════════════════════════════
# STREAMING
# ═══════════════════════════════════════════════════════════

class StreamEvent(BaseModel):
    """
    A single event from the SSE query stream.

    Attributes:
        event: Event type – "text", "thinking", "thinking_end",
               "sources", "metadata", "error", "done".
        data: Parsed JSON payload of the event.
    """
    event: str
    data: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.event == "text"

    @property
    def is_done(self) -> bool:
        return self.event == "done"

    @property
    def is_error(self) -> bool:
        return self.event == "error"

    @property
    def content(self) -> str:
        """Shortcut: returns data['content'] for text/thinking events."""
        return self.data.get("content", "")

    def __str__(self) -> str:
        if self.is_text:
            return self.content
        return f"[{self.event}] {self.data}"


# ═══════════════════════════════════════════════════════════
# CONVERSATIONS
# ═══════════════════════════════════════════════════════════

class ConversationMessage(BaseModel):
    """A single message in a conversation."""
    role: str
    content: str
    timestamp: Optional[str] = None
    confidence: Optional[float] = None
    message_id: Optional[str] = None
    style: Optional[str] = None
    mcp_tools: Optional[List[Dict[str, Any]]] = None


class Conversation(BaseModel):
    """A conversation with its messages and metadata."""
    conversation_id: str
    title: str = ""
    messages: List[ConversationMessage] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[Union[str, float]] = None
    summary: Optional[str] = None
    file_attachments: List[Dict[str, Any]] = Field(default_factory=list)
    web_references: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("updated_at", mode="before")
    @classmethod
    def _coerce_updated_at(cls, v):
        """Accept both ISO string and epoch float from MongoDB."""
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        return v

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def user_messages(self) -> List[ConversationMessage]:
        return [m for m in self.messages if m.role == "human"]

    @property
    def assistant_messages(self) -> List[ConversationMessage]:
        return [m for m in self.messages if m.role == "assistant"]


class ConversationSummary(BaseModel):
    """Lightweight conversation metadata (for listing)."""
    conversation_id: str
    title: str = ""
    created_at: Optional[Union[str, float]] = None
    updated_at: Optional[Union[str, float]] = None

    @field_validator("updated_at", "created_at", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v):
        """Accept both ISO string and epoch float from MongoDB."""
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        return v


# ═══════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════

class HealthStatus(BaseModel):
    """TTKIA service health information."""
    status: str
    backend: str = "unknown"
    embedding: str = "unknown"
    qdrant: str = "unknown"
    detail: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        return self.status in ("healthy", "ok")


# ═══════════════════════════════════════════════════════════
# FEEDBACK
# ═══════════════════════════════════════════════════════════

class FeedbackResult(BaseModel):
    """Result of submitting feedback."""
    success: bool
    message: str = ""