"""
TTKIA Client – Main entry point for the SDK.

Uses /query_complete REST endpoint for all queries.
Supports both synchronous and asynchronous interfaces.
Authentication via API Key (X-API-Key) or Bearer JWT token.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

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
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
    ):
        if not api_key and not bearer_token:
            raise ValueError("Provide either api_key or bearer_token")

        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._verify_ssl = verify_ssl

        # Build headers (non-auth)
        headers = {"Content-Type": "application/json"}
        if api_key:
            if not api_key.startswith(_API_KEY_PREFIX):
                raise ValueError(f"API key must start with '{_API_KEY_PREFIX}'")
            headers["X-API-Key"] = api_key

        # Build auth for Bearer token (survives redirects)
        auth: Optional[httpx.Auth] = None
        if bearer_token:
            auth = _BearerAuth(bearer_token)

        # Async + Sync clients (do NOT mix AsyncClient with asyncio.run per call)
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=True,
        )
        self._http_sync = httpx.Client(
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
        """Close the underlying HTTP clients (async)."""
        try:
            self._http_sync.close()
        except Exception:
            pass
        try:
            await self._http.aclose()
        except Exception:
            pass

    def close(self):
        """Close the underlying HTTP clients (sync)."""
        try:
            self._http_sync.close()
        except Exception:
            pass
        try:
            # Close async client safely (if we're already in a running loop, schedule it)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                loop.create_task(self._http.aclose())
            else:
                asyncio.run(self._http.aclose())
        except Exception:
            pass

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

    def _parse_query_response(self, data: Dict[str, Any], fallback_query: str) -> QueryResponse:
        """Parse backend QueryCompleteResponse into SDK QueryResponse."""
        timing_raw = data.get("timing", [])
        if isinstance(timing_raw, dict):
            timing_raw = [{k: v} for k, v in timing_raw.items()]

        return QueryResponse(
            success=data.get("success", False),
            conversation_id=data.get("conversation_id", ""),
            message_id=data.get("message_id", ""),
            query=data.get("query", fallback_query),
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
        resp = self._http_sync.get("/health")
        self._handle_error(resp)
        return HealthStatus(**resp.json())

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
        """Send a query via /query_complete and get the full response (async)."""
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
        return self._parse_query_response(data, fallback_query=query)

    def query(
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
        """Send a query via /query_complete and get the full response (sync)."""
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

        resp = self._http_sync.post("/query_complete", json=payload)
        self._handle_error(resp)
        data = resp.json()
        return self._parse_query_response(data, fallback_query=query)

    # ──────────────────────────────────────────────────────────
    # CONVERSATIONS
    # ──────────────────────────────────────────────────────────

    async def alist_conversations(self) -> List[ConversationSummary]:
        resp = await self._http.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        convs = data.get("conversations", [])
        return [ConversationSummary(**c) for c in convs]

    def list_conversations(self) -> List[ConversationSummary]:
        resp = self._http_sync.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        convs = data.get("conversations", [])
        return [ConversationSummary(**c) for c in convs]

    async def aget_conversation(self, conversation_id: str) -> Conversation:
        resp = await self._http.post(
            "/conversation-info",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return Conversation(**resp.json())

    def get_conversation(self, conversation_id: str) -> Conversation:
        resp = self._http_sync.post(
            "/conversation-info",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return Conversation(**resp.json())

    async def acreate_conversation(self, title: str = "SDK Conversation") -> str:
        # Backend currently ignores title for /new-workspace (kept for future)
        resp = await self._http.post("/new-workspace")
        self._handle_error(resp)
        return resp.json().get("conversation_id", "")

    def create_conversation(self, title: str = "SDK Conversation") -> str:
        resp = self._http_sync.post("/new-workspace")
        self._handle_error(resp)
        return resp.json().get("conversation_id", "")

    async def adelete_conversation(self, conversation_id: str) -> bool:
        resp = await self._http.post(
            "/delete_conversation",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        resp = self._http_sync.post(
            "/delete_conversation",
            json={"conversation_id": conversation_id},
        )
        self._handle_error(resp)
        return True

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

    def feedback(
        self,
        conversation_id: str,
        message_id: str,
        positive: bool,
        comment: str = "",
        inferred_environments: Optional[List[str]] = None,
    ) -> FeedbackResult:
        payload = {
            "feedback": positive,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "comment": comment,
            "inferred_environments": inferred_environments or [],
        }
        resp = self._http_sync.post("/feedback", json=payload)
        self._handle_error(resp)
        data = resp.json()
        return FeedbackResult(
            success=data.get("status") == "success",
            message=data.get("message", ""),
        )

    # ──────────────────────────────────────────────────────────
    # SOURCES & ENVIRONMENTS
    # ──────────────────────────────────────────────────────────

    async def aget_environments(self) -> List[str]:
        resp = await self._http.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        return data.get("environment", [])

    def get_environments(self) -> List[str]:
        resp = self._http_sync.get("/get_env")
        self._handle_error(resp)
        data = resp.json()
        return data.get("environment", [])

    async def aget_prompts(self) -> List[Dict[str, Any]]:
        resp = await self._http.get("/get_prompts")
        self._handle_error(resp)
        return resp.json().get("prompts", [])

    def get_prompts(self) -> List[Dict[str, Any]]:
        resp = self._http_sync.get("/get_prompts")
        self._handle_error(resp)
        return resp.json().get("prompts", [])

    async def aget_styles(self) -> List[Dict[str, Any]]:
        resp = await self._http.get("/get_styles")
        self._handle_error(resp)
        return resp.json().get("styles", [])

    def get_styles(self) -> List[Dict[str, Any]]:
        resp = self._http_sync.get("/get_styles")
        self._handle_error(resp)
        return resp.json().get("styles", [])

    # ──────────────────────────────────────────────────────────
    # EXPORT
    # ──────────────────────────────────────────────────────────

    async def aexport_conversation(self, conversation_id: str, output_path: str) -> str:
        resp = await self._http.get(
            f"/export-conversation/{conversation_id}",
            follow_redirects=True,
        )
        self._handle_error(resp)
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return output_path

    def export_conversation(self, conversation_id: str, output_path: str) -> str:
        resp = self._http_sync.get(
            f"/export-conversation/{conversation_id}",
            follow_redirects=True,
        )
        self._handle_error(resp)
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return output_path

    # ──────────────────────────────────────────────────────────
    # REPR
    # ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        auth = "api_key" if self._api_key else "bearer"
        return f"TTKIAClient(base_url={self.base_url!r}, auth={auth})"
