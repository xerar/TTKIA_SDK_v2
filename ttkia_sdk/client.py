"""
TTKIA Client – Main entry point for the SDK.

Uses /query_complete REST endpoint for all queries.
Supports both synchronous and asynchronous interfaces.
Authentication via API Key (X-API-Key) or Bearer JWT token.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from ttkia_sdk.models import (
    AuthenticationError,
    Conversation,
    ConversationSummary,
    FeedbackResult,
    HealthStatus,
    InsufficientScopeError,
    NotFoundError,
    QueryResponse,
    RateLimitError,
    Source,
    TimingInfo,
    TokenUsage,
    TTKIAError,
    StreamEvent,
)


_DEFAULT_TIMEOUT = 120.0
_API_KEY_PREFIX = "ttkia_sk_"


class _BearerAuth(httpx.Auth):
    """httpx Auth handler that injects Bearer token on every request, including redirects."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class TTKIAClient:
    """
    Official TTKIA Python client.

    All queries go through the /query_complete REST endpoint which
    executes the full pipeline and returns the consolidated response.

    Supports two authentication modes:
    - **API Key** (recommended): ``X-API-Key: ttkia_sk_...``
    - **Bearer Token** (App Token / JWT): ``Authorization: Bearer <token>``

    Examples:
        # Quick query
        client = TTKIAClient("https://ttkia.example.com", api_key="ttkia_sk_...")
        response = client.query("How do I configure a Fortinet VPN?")
        print(response.text)

        # With App Token (current system)
        client = TTKIAClient("https://ttkia.example.com", bearer_token="eyJhbG...")
        response = client.query("Explain SDWAN architecture")

        # Conversation continuity
        r1 = client.query("What is BGP?")
        r2 = client.query("How does it differ from OSPF?", conversation_id=r1.conversation_id)
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
    ):
        """
        Initialize the TTKIA client.

        Args:
            base_url: TTKIA server URL (e.g. ``https://ttkia.example.com``).
            api_key: API key starting with ``ttkia_sk_``. Future auth method.
            bearer_token: App Token or JWT. Current auth method.
            timeout: Request timeout in seconds (default 120).
            verify_ssl: Verify SSL certificates (default True).

        Raises:
            ValueError: If neither api_key nor bearer_token is provided.
        """
        if not api_key and not bearer_token:
            raise ValueError("Provide either api_key or bearer_token")

        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._bearer_token = bearer_token
        self._timeout = timeout

        # Build headers (non-auth)
        headers = {"Content-Type": "application/json"}
        if api_key:
            if not api_key.startswith(_API_KEY_PREFIX):
                raise ValueError(f"API key must start with '{_API_KEY_PREFIX}'")
            headers["X-API-Key"] = api_key

        # Build auth for Bearer token (survives redirects)
        auth = None
        if bearer_token:
            auth = _BearerAuth(bearer_token)

        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=True,
        )

    # ──────────────────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────────────────

    async def aclose(self):
        """Close the underlying HTTP client."""
        try:
            await self._http.aclose()
        except Exception:
            pass  # Best-effort cleanup

    def close(self):
        """Close the underlying HTTP client (sync)."""
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                loop.create_task(self._http.aclose())
            else:
                # No running loop — create one just for cleanup
                try:
                    asyncio.run(self._http.aclose())
                except RuntimeError:
                    pass
        except Exception:
            pass  # Best-effort cleanup

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ──────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────────────────

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise the appropriate exception based on HTTP status."""
        if response.is_success:
            return

        status = response.status_code
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text

        if status == 401:
            raise AuthenticationError(
                "Authentication failed", status_code=status, detail=detail
            )
        elif status == 403:
            raise InsufficientScopeError(
                "Insufficient permissions", status_code=status, detail=detail
            )
        elif status == 404:
            raise NotFoundError("Resource not found", status_code=status, detail=detail)
        elif status == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise RateLimitError(
                "Rate limit exceeded",
                retry_after=retry_after,
                status_code=status,
                detail=detail,
            )
        else:
            raise TTKIAError(f"HTTP {status}", status_code=status, detail=detail)

    def _sync(self, coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    # ──────────────────────────────────────────────────────────
    # HEALTH
    # ──────────────────────────────────────────────────────────

    async def ahealth(self) -> HealthStatus:
        """Check TTKIA service health (async)."""
        resp = await self._http.get("/health")
        self._handle_error(resp)
        return HealthStatus(**resp.json())

    def health(self) -> HealthStatus:
        """Check TTKIA service health (sync)."""
        return self._sync(self.ahealth())

    # ──────────────────────────────────────────────────────────
    # QUERY (via /query_complete)
    # ──────────────────────────────────────────────────────────

    async def aquery(
        self,
        query: str,
        *,
        conversation_id: Optional[str] = None,
        prompt: str = "default",
        style: str = "concise",
        web_search: bool = False,
        sources: Optional[List[str]] = None,
        teacher_mode: bool = False,
        title: Optional[str] = None,
    ) -> QueryResponse:
        """
        Send a query via /query_complete and get the full response (async).

        Args:
            query: The question to ask TTKIA.
            conversation_id: Continue an existing conversation.
            prompt: Prompt template to use (default: "default").
            style: Response style ("concise", "detailed", etc.).
            web_search: Enable web search augmentation.
            sources: Filter retrieval to specific document sources.
            teacher_mode: Enable Chain of Thought reasoning.
            title: Title for new conversations.

        Returns:
            QueryResponse with the full answer, sources, and metadata.

        Raises:
            AuthenticationError: Invalid or expired token.
            RateLimitError: Too many requests.
            TTKIAError: Any other server error.
        """
        payload = {
            "query": query,
            "prompt": prompt,
            "style": style,
            "web_search": web_search,
            "teacher_mode": teacher_mode,
            "sources": sources or [],
            "attached_files": [],
            "attached_urls": [],
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if title:
            payload["title"] = title

        resp = await self._http.post("/query_complete", json=payload)
        self._handle_error(resp)

        data = resp.json()

        # Parse timing - can be list or dict from backend
        timing_raw = data.get("timing", [])
        if isinstance(timing_raw, dict):
            timing_raw = [{k: v} for k, v in timing_raw.items()]

        return QueryResponse(
            success=data.get("success", False),
            conversation_id=data.get("conversation_id", ""),
            message_id=data.get("message_id", ""),
            query=data.get("query", query),
            response_text=data.get("response_text", ""),
            confidence=data.get("confidence"),
            recommended_response=data.get("recommended_response"),
            query_extended=data.get("query_extended"),
            token_usage=TokenUsage(
                input_tokens=data.get("token_counts", {}).get("input", 0),
                output_tokens=data.get("token_counts", {}).get("output", 0),
            ),
            timing=TimingInfo(raw=timing_raw),
            inferred_environments=data.get("inferred_environments", []),
            docs=[Source(**d) for d in data.get("docs", [])],
            webs=[Source(**w) for w in data.get("webs", [])],
            links=data.get("links", []),
            thinking_process=data.get("thinking_process", []),
            error=data.get("error"),
        )

    def query(self, query: str, **kwargs) -> QueryResponse:
        """Send a query and get the complete response (sync). See ``aquery`` for args."""
        return self._sync(self.aquery(query, **kwargs))

    # ──────────────────────────────────────────────────────────
    # CONVERSATIONS
    # ──────────────────────────────────────────────────────────

    async def alist_conversations(self) -> List[ConversationSummary]:
        """List all conversations for the current user (async)."""
        resp = await self._http.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        convs = data.get("conversations", [])
        return [ConversationSummary(**c) for c in convs]

    def list_conversations(self) -> List[ConversationSummary]:
        """List all conversations (sync)."""
        return self._sync(self.alist_conversations())

    async def aget_conversation(self, conversation_id: str) -> Conversation:
        """Get a full conversation with messages (async)."""
        resp = await self._http.post(
            "/conversation-info",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return Conversation(**resp.json())

    def get_conversation(self, conversation_id: str) -> Conversation:
        """Get a full conversation (sync)."""
        return self._sync(self.aget_conversation(conversation_id))

    async def acreate_conversation(self, title: str = "SDK Conversation") -> str:
        """Create a new empty conversation and return its ID (async)."""
        resp = await self._http.post("/new-workspace")
        self._handle_error(resp)
        return resp.json().get("conversation_id", "")

    def create_conversation(self, title: str = "SDK Conversation") -> str:
        """Create a new conversation (sync)."""
        return self._sync(self.acreate_conversation(title))

    async def adelete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation (async)."""
        resp = await self._http.post(
            "/delete_conversation",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation (sync)."""
        return self._sync(self.adelete_conversation(conversation_id))

    # ──────────────────────────────────────────────────────────
    # FEEDBACK
    # ──────────────────────────────────────────────────────────

    async def afeedback(
        self,
        conversation_id: str,
        message_id: str,
        positive: bool,
        comment: str = "",
        inferred_environments: Optional[List[str]] = None,
    ) -> FeedbackResult:
        """Submit feedback for a response (async)."""
        payload = {
            "feedback": positive,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "comment": comment,
            "inferred_environments": inferred_environments or [],
        }
        resp = await self._http.post("/feedback", json=payload)
        self._handle_error(resp)
        data = resp.json()
        return FeedbackResult(
            success=data.get("status") == "success",
            message=data.get("message", ""),
        )

    def feedback(self, conversation_id: str, message_id: str, positive: bool, **kwargs) -> FeedbackResult:
        """Submit feedback (sync)."""
        return self._sync(
            self.afeedback(conversation_id, message_id, positive, **kwargs)
        )

    # ──────────────────────────────────────────────────────────
    # SOURCES & ENVIRONMENTS
    # ──────────────────────────────────────────────────────────

    async def aget_environments(self) -> List[str]:
        """Get available environments for the current user (async)."""
        resp = await self._http.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        return data.get("environment", [])

    def get_environments(self) -> List[str]:
        """Get available environments (sync)."""
        return self._sync(self.aget_environments())

    async def aget_prompts(self) -> List[Dict[str, Any]]:
        """Get available prompt templates (async)."""
        resp = await self._http.get("/get_prompts")
        self._handle_error(resp)
        return resp.json().get("prompts", [])

    def get_prompts(self) -> List[Dict[str, Any]]:
        """Get available prompts (sync)."""
        return self._sync(self.aget_prompts())

    async def aget_styles(self) -> List[Dict[str, Any]]:
        """Get available response styles (async)."""
        resp = await self._http.get("/get_styles")
        self._handle_error(resp)
        return resp.json().get("styles", [])

    def get_styles(self) -> List[Dict[str, Any]]:
        """Get available styles (sync)."""
        return self._sync(self.aget_styles())

    # ──────────────────────────────────────────────────────────
    # EXPORT
    # ──────────────────────────────────────────────────────────

    async def aexport_conversation(
        self, conversation_id: str, output_path: str
    ) -> str:
        """
        Export a conversation as a ZIP file (async).

        Args:
            conversation_id: Conversation to export.
            output_path: Path to save the ZIP file.

        Returns:
            The output_path where the file was saved.
        """
        resp = await self._http.get(
            f"/export-conversation/{conversation_id}",
            follow_redirects=True,
        )
        self._handle_error(resp)

        with open(output_path, "wb") as f:
            f.write(resp.content)

        return output_path

    def export_conversation(self, conversation_id: str, output_path: str) -> str:
        """Export a conversation as ZIP (sync)."""
        return self._sync(self.aexport_conversation(conversation_id, output_path))

    # ──────────────────────────────────────────────────────────
    # REPR
    # ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        auth = "api_key" if self._api_key else "bearer"
        return f"TTKIAClient(base_url={self.base_url!r}, auth={auth})"

    # ──────────────────────────────────────────────────────────
    # STREAMING QUERY (via /query_stream SSE)
    # ──────────────────────────────────────────────────────────

    async def aquery_stream(
        self,
        query: str,
        *,
        conversation_id: Optional[str] = None,
        prompt: str = "default",
        style: str = "concise",
        web_search: bool = False,
        sources: Optional[List[str]] = None,
        teacher_mode: bool = False,
        title: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Send a query via /query_stream and yield events as they arrive (async).

        Yields StreamEvent objects with these event types:
            - "text"         → response text chunk (use event.content)
            - "thinking"     → CoT reasoning chunk
            - "thinking_end" → end of reasoning phase
            - "sources"      → docs, webs, links
            - "metadata"     → conversation_id, confidence, timing, tokens
            - "error"        → pipeline error
            - "done"         → stream finished

        Example:
            async for event in client.aquery_stream("What is BGP?"):
                if event.is_text:
                    print(event.content, end="", flush=True)
                elif event.event == "metadata":
                    print(f"\\nConfidence: {event.data['confidence']}")
        """
        import json as _json

        payload = {
            "query": query,
            "prompt": prompt,
            "style": style,
            "web_search": web_search,
            "teacher_mode": teacher_mode,
            "sources": sources or [],
            "attached_files": [],
            "attached_urls": [],
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if title:
            payload["title"] = title

        async with self._http.stream("POST", "/query_stream", json=payload) as resp:
            if not resp.is_success:
                # Leer body completo para error
                await resp.aread()
                self._handle_error(resp)

            # Parser SSE manual (líneas "event:" y "data:")
            current_event = "message"
            current_data = ""

            async for line in resp.aiter_lines():
                line = line.rstrip("\n").rstrip("\r")

                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    current_data = line[5:].strip()
                elif line == "":
                    # Línea vacía = fin del evento SSE
                    if current_data:
                        try:
                            parsed = _json.loads(current_data)
                        except _json.JSONDecodeError:
                            parsed = {"raw": current_data}

                        yield StreamEvent(event=current_event, data=parsed)

                        if current_event == "done":
                            return

                    current_event = "message"
                    current_data = ""

    def query_stream(self, query: str, **kwargs):
        """
        Send a query via /query_stream and yield events (sync generator).

        Same args as aquery_stream(). Runs the async iterator in a thread.

        Example:
            for event in client.query_stream("What is BGP?"):
                if event.is_text:
                    print(event.content, end="", flush=True)
        """
        import asyncio
        import concurrent.futures
        import queue

        q = queue.Queue()
        sentinel = object()

        async def _consume():
            try:
                async for event in self.aquery_stream(query, **kwargs):
                    q.put(event)
            except Exception as e:
                q.put(e)
            finally:
                q.put(sentinel)

        def _run():
            asyncio.run(_consume())

        thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        thread.submit(_run)

        try:
            while True:
                item = q.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            thread.shutdown(wait=False)
