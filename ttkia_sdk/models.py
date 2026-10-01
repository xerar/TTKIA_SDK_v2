"""
TTKIA SDK – Data models.

All Pydantic models that represent API responses and data structures.
"""

from __future__ import annotations

import re
import warnings
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

# Marcas de cita que el backend deja en el texto: [doc:N], [link:N], [web:N].
# N es 1-based sobre las listas docs / links / webs de la misma respuesta.
# Mismo patrón que src/tools/citations.py del backend.
CITATION_RE = re.compile(r"\[(doc|link|web):(\d+)\]")


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
    """
    Raised on HTTP 429.

    DOS CAUSAS DISTINTAS COMPARTEN EL CÓDIGO, y no se reintentan igual:

      · Frecuencia (30 peticiones/minuto por usuario). El backend manda
        `Retry-After`; esperar ese tiempo resuelve. `retryable` es True.

      · Presupuesto agotado — de la API Key o del usuario. NO manda
        `Retry-After`, y la ventana de gasto se mide en DÍAS. `retryable` es
        False: reintentar cada minuto no levanta nada y cada intento cuesta
        una agregación sobre `resources_usage` en el servidor.

    `retry_after` conserva su valor y su default para no romper a nadie, pero
    solo es significativo cuando `retryable` es True. Antes de dormir, mira
    `retryable`.
    """

    def __init__(
        self,
        message: str,
        retry_after: int = 60,
        status_code: int = 429,
        retryable: bool = True,
    ):
        self.retry_after = retry_after
        self.retryable = retryable
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
    """A source referenced in the response: document, internal link or web result.

    Built from the backend metadata as-is. Unknown keys are ignored; `_score`
    (cosine similarity) is exposed as `relevance`.
    """
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    source: str = ""
    environment: str = ""
    page: Optional[int] = None
    section_path: str = ""
    author: str = ""
    tag: str = ""
    url: str = ""
    description: str = ""
    relevance: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, v):
        # Backends <= 6.4.0 declaraban `links` como List[str].
        if isinstance(v, str):
            return {"title": v, "source": v}
        if not isinstance(v, dict):
            return v
        d = dict(v)
        if d.get("relevance") is None and d.get("_score") is not None:
            d["relevance"] = d["_score"]
        page = d.get("page")
        if page is not None and not isinstance(page, int):
            try:
                d["page"] = int(str(page).strip())
            except (TypeError, ValueError):
                d["page"] = None
        # Campos de texto: el backend puede mandar None o números
        for key in ("title", "source", "environment", "section_path",
                    "author", "tag", "url", "description"):
            if key in d and d[key] is not None and not isinstance(d[key], str):
                d[key] = str(d[key])
            elif key in d and d[key] is None:
                d[key] = ""
        return d

    @property
    def is_web(self) -> bool:
        for value in (self.url, self.source):
            s = (value or "").lower()
            if s.startswith("http://") or s.startswith("https://"):
                return True
        return False

    @property
    def label(self) -> str:
        """Human-readable reference: title, page and section when available."""
        parts = [self.title or self.source or self.url or "(unknown)"]
        if self.page:
            parts.append(f"p. {self.page}")
        if self.section_path:
            parts.append(self.section_path)
        return " — ".join(parts)

    def __str__(self) -> str:
        return self.title or self.source or self.url or "(unknown)"


class Citation(BaseModel):
    """A citation marker found in the response text, resolved to its source."""
    kind: str                      # "doc" | "link" | "web"
    index: int                     # 1-based, as written in the text
    source: Optional[Source] = None

    @property
    def marker(self) -> str:
        return f"[{self.kind}:{self.index}]"

    def __str__(self) -> str:
        return f"{self.marker} {self.source.label if self.source else '(missing)'}"


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
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    def __str__(self) -> str:
        if self.is_success:
            preview = str(self.result)[:100]
            return f"✅ {self.name}: {preview}"
        return f"❌ {self.name}: {self.error or self.status}"


class ModelNotice(BaseModel):
    """Aviso de degradación de modelo. Backends anteriores no lo envían.

    La petición no la sirvió el modelo previsto. `reason`:
      - "refusal": el filtro de seguridad del proveedor declinó y se repitió
        con el modelo de reserva (`refusal_fallback`).
      - "budget": tope de gasto del modelo avanzado alcanzado.
    En /code/stream llega como evento `model_notice` con estos mismos campos.
    """
    reason: str = ""
    from_model: str = ""
    to_model: str = ""
    message: str = ""

    def __str__(self) -> str:
        return self.message or f"{self.from_model} → {self.to_model} ({self.reason})"


# ═══════════════════════════════════════════════════════════
# QUERY RESPONSE
# ═══════════════════════════════════════════════════════════

class QueryResponse(BaseModel):
    """
    Full response from a /query_complete request.

    Every part travels in its own field. The answer is `text`; the evaluation
    of that answer is `confidence` + `evaluation`, never inside `text`.
    """
    model_config = ConfigDict(populate_by_name=True)

    success: bool
    conversation_id: str = ""
    message_id: str = ""
    query: str = ""
    text: str = Field("", alias="response_text")
    confidence: Optional[float] = None
    # Explicación del evaluador sobre la respuesta. El backend <= 6.4.0 la
    # manda como `recommended_response` (nombre engañoso: no es una respuesta).
    evaluation: Optional[str] = Field(
        None, validation_alias=AliasChoices("evaluation", "recommended_response")
    )
    query_extended: Optional[str] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    timing: TimingInfo = Field(default_factory=TimingInfo)
    inferred_environments: List[str] = Field(default_factory=list)
    docs: List[Source] = Field(default_factory=list)
    # Obsoleto en /query_complete: venía de las webs descargadas por entorno,
    # retiradas en 6.4.0. El backend 6.4.1 deja de enviarlo; se conserva vacío
    # por compatibilidad. (En el historial sí sigue: Deep Research cita sus
    # páginas web como [link:N] → ConversationMessage.links.)
    links: List[Source] = Field(default_factory=list)
    webs: List[Source] = Field(default_factory=list)
    # Razonamiento del modelo (backend >= 6.4.1). Vacío si el backend no lo envía.
    reasoning: str = ""
    # Obsoleto: en backends <= 6.4.0 solo trae marcadores de fase, no el razonamiento.
    thinking_process: List[str] = Field(default_factory=list)
    mcp_tools: List[MCPToolResult] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    # Degradaciones de modelo de esta petición. Hoy solo lo rellena
    # code_query (/code/query); vacío si no hubo o el backend no lo envía.
    notices: List[ModelNotice] = Field(default_factory=list)
    error: Optional[str] = None

    # ── Estado ────────────────────────────────────────────────

    @property
    def is_error(self) -> bool:
        return not self.success or self.error is not None

    @property
    def used_mcp(self) -> bool:
        """True if MCP tools were used in this response."""
        return len(self.mcp_tools) > 0

    @property
    def recommended_response(self) -> Optional[str]:
        """Deprecated: use `evaluation`. It never was an alternative answer."""
        warnings.warn(
            "QueryResponse.recommended_response is deprecated; use .evaluation",
            DeprecationWarning, stacklevel=2,
        )
        return self.evaluation

    # ── Fuentes y citas ───────────────────────────────────────

    @property
    def sources(self) -> List[Source]:
        """All sources, in order: docs, internal links, web results."""
        return self.docs + self.links + self.webs

    def _source_for(self, kind: str, index: int) -> Optional[Source]:
        pool = {"doc": self.docs, "link": self.links, "web": self.webs}[kind]
        return pool[index - 1] if 1 <= index <= len(pool) else None

    @property
    def citations(self) -> List[Citation]:
        """Citation markers present in `text`, deduplicated, in order of first appearance."""
        seen, out = set(), []
        for m in CITATION_RE.finditer(self.text or ""):
            key = (m.group(1), int(m.group(2)))
            if key in seen:
                continue
            seen.add(key)
            out.append(Citation(kind=key[0], index=key[1],
                                source=self._source_for(*key)))
        return out

    @property
    def cited_sources(self) -> List[Source]:
        """Only the sources actually cited in `text`, without duplicates."""
        out: List[Source] = []
        for c in self.citations:
            if c.source is not None and c.source not in out:
                out.append(c.source)
        return out

    @property
    def text_plain(self) -> str:
        """`text` with markers reduced to `[N]` (``[doc:3]`` → ``[3]``)."""
        return CITATION_RE.sub(lambda m: f"[{m.group(2)}]", self.text or "")

    def text_with_references(self) -> str:
        """`text_plain` plus a References section listing only the cited sources.

        Same format as the Word documents generated by TTKIA
        (src/tools/citations.py → build_references_section).
        """
        content = self.text or ""
        cited = {"doc": set(), "link": set(), "web": set()}
        for m in CITATION_RE.finditer(content):
            cited[m.group(1)].add(int(m.group(2)))
        cleaned = self.text_plain
        if not any(cited.values()):
            return cleaned

        parts = [cleaned, "\n\n---\n\n## Referencias\n"]
        sections = (("doc", "Documentos", self.docs),
                    ("link", "Enlaces internos", self.links),
                    ("web", "Web", self.webs))
        for kind, heading, pool in sections:
            if not cited[kind]:
                continue
            parts.append(f"\n### {heading}\n")
            for idx in sorted(cited[kind]):
                if idx > len(pool):
                    continue
                s = pool[idx - 1]
                title = s.title or s.source or ("Web result" if kind == "web" else "Unknown")
                line = f"[{idx}] **{title}**"
                if kind == "doc":
                    if s.author:
                        line += f" — *{s.author}*"
                    if s.page:
                        line += f" — pág. {s.page}"
                    if s.section_path:
                        line += f", {s.section_path}"
                elif s.url:
                    line += f"  \n    {s.url}"
                parts.append(line + "  \n")
        return "".join(parts)

    # ── Representación ────────────────────────────────────────

    def summary(self, max_chars: int = 200) -> str:
        """One-line preview for logs: confidence + start of the answer."""
        if self.is_error:
            return f"[ERROR] {self.error}"
        text = self.text or ""
        preview = text[:max_chars] + "..." if len(text) > max_chars else text
        return f"[{self.confidence or 0:.0%}] {preview}"

    def __str__(self) -> str:
        # El texto completo y nada más: quien hace print(response) o
        # f"{response}" recibe la respuesta, no un resumen con la valoración.
        if self.is_error:
            return f"[ERROR] {self.error}"
        return self.text or ""


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
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    role: str
    content: str = ""
    timestamp: Optional[str] = None
    confidence: Optional[float] = None
    evaluation: Optional[str] = Field(
        None, validation_alias=AliasChoices("evaluation", "recommended_response")
    )
    message_id: Optional[str] = None
    style: Optional[str] = None
    mcp_tools: Optional[List[Dict[str, Any]]] = None
    docs: List[Source] = Field(default_factory=list)
    links: List[Source] = Field(default_factory=list)
    webs: List[Source] = Field(default_factory=list)

    @field_validator("docs", "links", "webs", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        return v or []

    @field_validator("content", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        # Un turno de agente en diferido que no llegó a sustituirse puede
        # quedar sin cuerpo; no debe romper la lectura de la conversación.
        return v if v is not None else ""


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