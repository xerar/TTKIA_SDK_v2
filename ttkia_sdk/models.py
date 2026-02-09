"""
Data models for TTKIA SDK responses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════

class TTKIAError(Exception):
    """Base exception for all TTKIA SDK errors."""

    def __init__(self, message: str, status_code: int = 0, detail: str = ""):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)

    def __str__(self) -> str:
        base = f"[{self.status_code}] {self.message}" if self.status_code else self.message
        return f"{base} – {self.detail}" if self.detail else base


class AuthenticationError(TTKIAError):
    """Raised when token is invalid, expired, or revoked."""
    pass


class RateLimitError(TTKIAError):
    """Raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: int = 60, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class NotFoundError(TTKIAError):
    """Raised when a resource is not found."""
    pass


class InsufficientScopeError(TTKIAError):
    """Raised when the API key lacks the required scope."""
    pass


# ═══════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════

class Source(BaseModel):
    """A document source referenced in the response."""
    title: Optional[str] = None
    source: Optional[str] = None
    environment: Optional[str] = None
    tag: Optional[str] = None
    page: Optional[int] = None

    @property
    def is_web(self) -> bool:
        return self.tag == "internet" if self.tag else False


class TokenUsage(BaseModel):
    """Token consumption for a query."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TimingInfo(BaseModel):
    """Execution timing breakdown."""
    raw: List[Dict[str, float]] = Field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(v for step in self.raw for v in step.values())

    def get_step(self, name: str) -> Optional[float]:
        for step in self.raw:
            if name in step:
                return step[name]
        return None

    def summary(self) -> Dict[str, float]:
        result = {}
        for step in self.raw:
            result.update(step)
        return result


class QueryResponse(BaseModel):
    """
    Complete response from a TTKIA query via /query_complete.
    
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
    error: Optional[str] = None

    model_config = {"populate_by_name": True}

    @property
    def is_error(self) -> bool:
        return not self.success or self.error is not None

    @property
    def sources(self) -> List[Source]:
        """All sources (docs + webs) combined."""
        return self.docs + self.webs

    def __str__(self) -> str:
        if self.is_error:
            return f"[ERROR] {self.error}"
        preview = self.text[:200] + "..." if len(self.text) > 200 else self.text
        return f"[{self.confidence or 0:.0%}] {preview}"


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


class ConversationMessage(BaseModel):
    """A single message in a conversation."""
    role: str
    content: str
    timestamp: Optional[str] = None
    confidence: Optional[float] = None
    message_id: Optional[str] = None
    style: Optional[str] = None


class Conversation(BaseModel):
    """A conversation with its messages and metadata."""
    conversation_id: str
    title: str = ""
    messages: List[ConversationMessage] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    summary: Optional[str] = None
    file_attachments: List[Dict[str, Any]] = Field(default_factory=list)
    web_references: List[Dict[str, Any]] = Field(default_factory=list)

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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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


class FeedbackResult(BaseModel):
    """Result of submitting feedback."""
    success: bool
    message: str = ""

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