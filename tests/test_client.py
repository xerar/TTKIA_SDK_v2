"""
Tests for TTKIA SDK (REST-only).

Run: pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ttkia_sdk import TTKIAClient, QueryResponse, TTKIAError, AuthenticationError, RateLimitError
from ttkia_sdk.models import Source, TokenUsage, TimingInfo, HealthStatus, ConversationSummary


# ═══════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def api_response():
    """Standard /query_complete response from the backend."""
    return {
        "success": True,
        "conversation_id": "conv-123",
        "message_id": "msg-456",
        "query": "What is BGP?",
        "response_text": "BGP is a path vector protocol...",
        "confidence": 0.85,
        "recommended_response": None,
        "query_extended": None,
        "token_counts": {"input": 500, "output": 200},
        "timing": [{"retrieve": 0.5}, {"textual": 2.1}, {"analyze": 0.3}],
        "inferred_environments": ["networking"],
        "docs": [{"title": "BGP Guide", "source": "bgp.pdf", "environment": "networking"}],
        "webs": [],
        "links": [],
        "thinking_process": [],
        "error": None,
    }


def _mock_http_response(data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.is_success = 200 <= status_code < 300
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = str(data)
    resp.headers = {}
    return resp


# ═══════════════════════════════════════════════════════════
# CLIENT INIT
# ═══════════════════════════════════════════════════════════

class TestClientInit:

    def test_bearer_token_auth(self):
        client = TTKIAClient("https://test.com", bearer_token="eyJhbG...")
        assert client._bearer_token == "eyJhbG..."
        assert client._http.auth is not None  # Auth handled via httpx.Auth
        client.close()

    def test_api_key_auth(self):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_abc123")
        assert client._api_key == "ttkia_sk_abc123"
        assert "X-API-Key" in client._http.headers
        client.close()

    def test_both_auth_methods(self):
        """Both can be provided simultaneously."""
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_abc", bearer_token="jwt")
        assert "X-API-Key" in client._http.headers
        assert client._http.auth is not None  # Bearer via httpx.Auth
        client.close()

    def test_no_auth_raises(self):
        with pytest.raises(ValueError, match="Provide either"):
            TTKIAClient("https://test.com")

    def test_invalid_api_key_prefix(self):
        with pytest.raises(ValueError, match="must start with"):
            TTKIAClient("https://test.com", api_key="bad_key")

    def test_url_trailing_slash_stripped(self):
        client = TTKIAClient("https://test.com/", bearer_token="tok")
        assert client.base_url == "https://test.com"
        client.close()

    def test_repr(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        assert "bearer" in repr(client)
        client.close()


# ═══════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════

class TestModels:

    def test_query_response_parsing(self, api_response):
        resp = QueryResponse(
            success=api_response["success"],
            conversation_id=api_response["conversation_id"],
            response_text=api_response["response_text"],
            confidence=api_response["confidence"],
            token_usage=TokenUsage(
                input_tokens=api_response["token_counts"]["input"],
                output_tokens=api_response["token_counts"]["output"],
            ),
            timing=TimingInfo(raw=api_response["timing"]),
            docs=[Source(**d) for d in api_response["docs"]],
        )
        assert resp.text == "BGP is a path vector protocol..."
        assert resp.confidence == 0.85
        assert resp.token_usage.total == 700
        assert resp.timing.total_seconds == pytest.approx(2.9)
        assert resp.timing.get_step("retrieve") == 0.5
        assert not resp.is_error
        assert len(resp.sources) == 1

    def test_query_response_error(self):
        resp = QueryResponse(success=False, error="Timeout")
        assert resp.is_error
        assert "ERROR" in str(resp)

    def test_query_response_str(self):
        resp = QueryResponse(success=True, response_text="Hello world", confidence=0.9)
        assert "90%" in str(resp)

    def test_source_is_web(self):
        assert Source(tag="internet").is_web
        assert not Source(tag="bookshelf").is_web
        assert not Source().is_web

    def test_token_usage(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total == 150

    def test_timing_summary(self):
        timing = TimingInfo(raw=[{"retrieve": 0.5}, {"textual": 2.0}, {"analyze": 0.3}])
        summary = timing.summary()
        assert summary == {"retrieve": 0.5, "textual": 2.0, "analyze": 0.3}
        assert timing.get_step("nonexistent") is None

    def test_health_status(self):
        assert HealthStatus(status="healthy").is_healthy
        assert not HealthStatus(status="degraded").is_healthy

    def test_conversation_summary(self):
        cs = ConversationSummary(conversation_id="abc", title="Test")
        assert cs.conversation_id == "abc"


# ═══════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════

class TestExceptions:

    def test_ttkia_error_str(self):
        err = TTKIAError("Failed", status_code=500, detail="Internal")
        assert "[500]" in str(err)
        assert "Internal" in str(err)

    def test_error_without_status(self):
        err = TTKIAError("Generic error")
        assert "Generic error" in str(err)

    def test_auth_error_is_ttkia_error(self):
        assert isinstance(AuthenticationError("Bad"), TTKIAError)

    def test_rate_limit_retry_after(self):
        err = RateLimitError("Too fast", retry_after=30, status_code=429)
        assert err.retry_after == 30


# ═══════════════════════════════════════════════════════════
# CLIENT METHODS (mocked HTTP)
# ═══════════════════════════════════════════════════════════

class TestClientMethods:

    @pytest.mark.asyncio
    async def test_aquery_success(self, api_response):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.post.return_value = _mock_http_response(api_response)

        result = await client.aquery("What is BGP?")

        assert isinstance(result, QueryResponse)
        assert result.text == "BGP is a path vector protocol..."
        assert result.confidence == 0.85
        assert result.conversation_id == "conv-123"
        assert result.token_usage.total == 700

        # Verify correct endpoint called
        client._http.post.assert_called_once()
        call_args = client._http.post.call_args
        assert call_args[0][0] == "/query_complete"

        await client.aclose()

    @pytest.mark.asyncio
    async def test_aquery_with_options(self, api_response):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.post.return_value = _mock_http_response(api_response)

        await client.aquery(
            "test",
            conversation_id="conv-1",
            prompt="expert",
            style="detailed",
            web_search=True,
            teacher_mode=True,
            sources=["firewall.pdf"],
            title="My Query",
        )

        payload = client._http.post.call_args[1]["json"]
        assert payload["conversation_id"] == "conv-1"
        assert payload["prompt"] == "expert"
        assert payload["style"] == "detailed"
        assert payload["web_search"] is True
        assert payload["teacher_mode"] is True
        assert payload["sources"] == ["firewall.pdf"]
        assert payload["title"] == "My Query"

        await client.aclose()

    @pytest.mark.asyncio
    async def test_ahealth(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.get.return_value = _mock_http_response({"status": "healthy"})

        result = await client.ahealth()
        assert result.is_healthy
        await client.aclose()

    @pytest.mark.asyncio
    async def test_aget_environments(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.get.return_value = _mock_http_response({
            "environment": ["retail", "banking"],
            "conversations": [],
        })

        envs = await client.aget_environments()
        assert envs == ["retail", "banking"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_error_401(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.post.return_value = _mock_http_response(
            {"detail": "Invalid token"}, status_code=401
        )

        with pytest.raises(AuthenticationError):
            await client.aquery("test")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_error_429(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        mock_resp = _mock_http_response({"detail": "Rate limit"}, status_code=429)
        mock_resp.headers = {"Retry-After": "30"}
        client._http.post.return_value = mock_resp

        with pytest.raises(RateLimitError) as exc_info:
            await client.aquery("test")
        assert exc_info.value.retry_after == 30
        await client.aclose()

    @pytest.mark.asyncio
    async def test_error_500(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.post.return_value = _mock_http_response(
            {"detail": "Internal error"}, status_code=500
        )

        with pytest.raises(TTKIAError) as exc_info:
            await client.aquery("test")
        assert exc_info.value.status_code == 500
        await client.aclose()

    @pytest.mark.asyncio
    async def test_afeedback(self):
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        client._http.post.return_value = _mock_http_response({
            "status": "success",
            "message": "Feedback saved",
        })

        result = await client.afeedback("conv-1", "msg-1", True, comment="Great")
        assert result.success
        assert result.message == "Feedback saved"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_timing_dict_format(self):
        """Backend can return timing as dict instead of list."""
        client = TTKIAClient("https://test.com", bearer_token="tok")
        client._http = AsyncMock()
        data = {
            "success": True, "conversation_id": "c", "message_id": "m",
            "query": "q", "response_text": "r", "confidence": 0.5,
            "timing": {"retrieve": 0.5, "textual": 2.0},  # dict format
            "token_counts": {"input": 10, "output": 20},
            "docs": [], "webs": [], "links": [],
        }
        client._http.post.return_value = _mock_http_response(data)

        result = await client.aquery("test")
        assert result.timing.total_seconds == pytest.approx(2.5)
        await client.aclose()
