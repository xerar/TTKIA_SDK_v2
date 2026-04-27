"""
Tests for TTKIA SDK (REST-only).

Run: pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ttkia_sdk import TTKIAClient, QueryResponse, TTKIAError, AuthenticationError, RateLimitError
from ttkia_sdk.models import Source, TokenUsage, TimingInfo, HealthStatus, ConversationSummary, MCPToolResult


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
        "mcp_tools": [],
        "follow_ups": [],
        "error": None,
    }


@pytest.fixture
def api_response_with_mcp():
    """Response from /query_complete that used MCP tools."""
    return {
        "success": True,
        "conversation_id": "conv-789",
        "message_id": "msg-012",
        "query": "Who has CCNA certification?",
        "response_text": "Based on the directory, 5 employees have CCNA...",
        "confidence": 0.92,
        "recommended_response": None,
        "query_extended": None,
        "token_counts": {"input": 800, "output": 350},
        "timing": [{"retrieve": 0.3}, {"mcp_tools": 1.2}, {"textual": 2.5}],
        "inferred_environments": ["networking"],
        "docs": [],
        "webs": [],
        "links": [],
        "thinking_process": [],
        "mcp_tools": [
            {
                "name": "people_search",
                "status": "success",
                "args": {"certificacion": "CCNA"},
                "result": {"status": "success", "total": 5, "results": []}
            }
        ],
        "follow_ups": ["What other Cisco certifications exist?", "Who has CCNP?"],
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
        assert client._http.auth is not None
        client.close()

    def test_api_key_auth(self):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_abc123")
        assert client._api_key == "ttkia_sk_abc123"
        assert "X-API-Key" in client._http.headers
        client.close()

    def test_both_auth_methods(self):
        """Both can be provided simultaneously."""
        client = TTKIAClient("https://test.com", bearer_token="tok", api_key="ttkia_sk_key")
        assert client._bearer_token == "tok"
        assert client._api_key == "ttkia_sk_key"
        client.close()

    def test_no_auth_raises(self):
        with pytest.raises(TTKIAError):
            TTKIAClient("https://test.com")

    def test_no_url_raises(self):
        """No URL and no config file should raise."""
        with pytest.raises(TTKIAError):
            TTKIAClient(api_key="ttkia_sk_test")


# ═══════════════════════════════════════════════════════════
# QUERY RESPONSE PARSING
# ═══════════════════════════════════════════════════════════

class TestQueryParsing:

    def test_basic_response(self, api_response):
        qr = TTKIAClient._parse_query_response(api_response)
        assert qr.success is True
        assert qr.text == "BGP is a path vector protocol..."
        assert qr.confidence == 0.85
        assert len(qr.docs) == 1
        assert qr.token_usage.total == 700
        assert not qr.used_mcp

    def test_mcp_response(self, api_response_with_mcp):
        qr = TTKIAClient._parse_query_response(api_response_with_mcp)
        assert qr.success is True
        assert qr.used_mcp is True
        assert len(qr.mcp_tools) == 1
        assert qr.mcp_tools[0].name == "people_search"
        assert qr.mcp_tools[0].is_success
        assert qr.mcp_tools[0].args == {"certificacion": "CCNA"}
        assert len(qr.follow_ups) == 2

    def test_timing_dict_format(self):
        """Backend may return timing as dict instead of list."""
        data = {
            "success": True,
            "response_text": "test",
            "timing": {"retrieve": 0.5, "textual": 2.0},
            "token_counts": {},
        }
        qr = TTKIAClient._parse_query_response(data)
        assert qr.timing.get("retrieve") == 0.5

    def test_error_response(self):
        data = {"success": False, "response_text": "", "error": "Pipeline failed"}
        qr = TTKIAClient._parse_query_response(data)
        assert qr.is_error
        assert qr.error == "Pipeline failed"


# ═══════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════

class TestModels:

    def test_conversation_summary_string_dates(self):
        cs = ConversationSummary(
            conversation_id="abc",
            title="Test",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-02T00:00:00",
        )
        assert cs.updated_at == "2024-01-02T00:00:00"

    def test_conversation_summary_float_dates(self):
        """Backend update_memory.py writes time.time() as updated_at."""
        cs = ConversationSummary(
            conversation_id="abc",
            title="Test",
            created_at="2024-01-01T00:00:00",
            updated_at=1772621971.2031674,
        )
        # Should be coerced to ISO string
        assert isinstance(cs.updated_at, str)
        assert "T" in cs.updated_at

    def test_conversation_summary_none_dates(self):
        cs = ConversationSummary(conversation_id="abc")
        assert cs.updated_at is None
        assert cs.created_at is None

    def test_mcp_tool_result(self):
        t = MCPToolResult(name="people_search", status="success", args={"q": "test"}, result={"total": 5})
        assert t.is_success
        assert "people_search" in str(t)

    def test_mcp_tool_result_error(self):
        t = MCPToolResult(name="bad_tool", status="error")
        assert not t.is_success

    def test_source_is_web(self):
        s1 = Source(source="https://example.com", title="Web")
        s2 = Source(source="docs.pdf", title="Doc")
        assert s1.is_web
        assert not s2.is_web

    def test_health_status(self):
        h = HealthStatus(status="ok")
        assert h.is_healthy
        h2 = HealthStatus(status="degraded")
        assert not h2.is_healthy


# ═══════════════════════════════════════════════════════════
# API CALLS (mocked)
# ═══════════════════════════════════════════════════════════

class TestAPICalls:

    def test_query_sync(self, api_response):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(api_response)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        qr = client.query("What is BGP?")
        assert qr.success
        assert qr.text == "BGP is a path vector protocol..."
        client.close()

    def test_query_with_mcp(self, api_response_with_mcp):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(api_response_with_mcp)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        qr = client.query("Who has CCNA?")
        assert qr.success
        assert qr.used_mcp
        assert qr.mcp_tools[0].name == "people_search"
        client.close()

    def test_list_conversations_float_dates(self):
        """Regression: updated_at as float from MongoDB should not crash."""
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        env_data = {
            "environment": ["test"],
            "user": {
                "history_chat": {
                    "conversations": [
                        {
                            "conversation_id": "abc-123",
                            "title": "Test conv",
                            "created_at": "2024-01-01T00:00:00",
                            "updated_at": 1772621971.2031674,  # ← float from time.time()
                        }
                    ]
                }
            },
            "prompts": [],
            "styles": [],
        }
        mock_resp = _mock_http_response(env_data)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        convs = client.list_conversations()
        assert len(convs) == 1
        assert isinstance(convs[0].updated_at, str)
        client.close()

    def test_health(self):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response({"status": "ok"})
        client._http_sync.get = MagicMock(return_value=mock_resp)

        h = client.health()
        assert h.is_healthy
        client.close()

    def test_auth_error(self):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response({"detail": "Invalid token"}, 401)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError):
            client.query("test")
        client.close()

    def test_rate_limit(self):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response({"detail": "Too many requests"}, 429)
        mock_resp.headers = {"Retry-After": "30"}
        client._http_sync.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RateLimitError) as exc_info:
            client.query("test")
        assert exc_info.value.retry_after == 30
        client.close()


# ═══════════════════════════════════════════════════════════
# ATTACHMENTS
# ═══════════════════════════════════════════════════════════
 
class TestAttachments:
 
    @pytest.fixture
    def upload_response(self):
        """Respuesta estándar de /chat-upload."""
        return {
            "path": "/data/attachments/testuser/conv-123/router_config.txt",
            "name": "router_config.txt",
            "size": 512,
            "type": "text/plain",
            "stored_as": "embedded://conv-123/router_config.txt",
            "status": "completed",
            "conversation_id": "conv-123",
        }
 
    def test_upload_attachment_returns_metadata(self, upload_response, tmp_path):
        """upload_attachment llama a /chat-upload y devuelve el metadata limpio."""
        # Crear fichero temporal
        f = tmp_path / "router_config.txt"
        f.write_text("interface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.0\n")
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_response)
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        result = client.upload_attachment(str(f), "conv-123")
 
        # Verificar que se llamó a /chat-upload
        call_args = client._http_sync.post.call_args
        assert "/chat-upload" in str(call_args)
 
        # Verificar metadata devuelta
        assert result["name"] == "router_config.txt"
        assert result["size"] == 512
        assert result["type"] == "text/plain"
        assert result["status"] == "completed"
        assert "path" in result
 
        client.close()
 
    def test_upload_attachment_image(self, tmp_path):
        """Imágenes también se suben con el MIME correcto."""
        img = tmp_path / "diagram.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)  # PNG mínimo
 
        upload_resp = {
            "path": "/data/attachments/testuser/conv-123/diagram.png",
            "name": "diagram.png",
            "size": 58,
            "type": "image/png",
            "stored_as": "physical_file",
            "status": "completed",
            "conversation_id": "conv-123",
        }
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_resp)
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        result = client.upload_attachment(str(img), "conv-123")
 
        assert result["type"] == "image/png"
        assert result["status"] == "completed"
        client.close()
 
    def test_query_with_attached_files(self, api_response):
        """query() pasa attached_files en el payload JSON."""
        attachment = {
            "path": "/data/attachments/testuser/conv-123/config.txt",
            "name": "config.txt",
            "size": 512,
            "type": "text/plain",
            "status": "completed",
        }
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(api_response)
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        client.query(
            "Review this config",
            conversation_id="conv-123",
            attached_files=[attachment],
        )
 
        call_kwargs = client._http_sync.post.call_args.kwargs
        payload = call_kwargs.get("json", {})
        assert payload["attached_files"] == [attachment]
        assert payload["attached_urls"] == []
 
        client.close()
 
    def test_query_with_attached_urls(self, api_response):
        """query() pasa attached_urls en el payload JSON."""
        url_attachment = {
            "url": "https://sec.cloudapps.cisco.com/security/center/publicationListing.x",
            "name": "Cisco Security Advisories",
        }
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(api_response)
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        client.query(
            "Summarize this advisory",
            conversation_id="conv-123",
            attached_urls=[url_attachment],
        )
 
        call_kwargs = client._http_sync.post.call_args.kwargs
        payload = call_kwargs.get("json", {})
        assert payload["attached_urls"] == [url_attachment]
        assert payload["attached_files"] == []
 
        client.close()
 
    def test_query_default_no_attachments(self, api_response):
        """Sin adjuntos, el payload manda listas vacías (no None)."""
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(api_response)
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        client.query("What is BGP?")
 
        call_kwargs = client._http_sync.post.call_args.kwargs
        payload = call_kwargs.get("json", {})
        assert payload["attached_files"] == []
        assert payload["attached_urls"] == []
 
        client.close()
 
    def test_upload_attachment_error_propagates(self, tmp_path):
        """Un 400 del backend se convierte en TTKIAError."""
        f = tmp_path / "bad.xyz"
        f.write_text("data")
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(
            {"detail": "Unsupported file type: .xyz"}, 400
        )
        client._http_sync.post = MagicMock(return_value=mock_resp)
 
        with pytest.raises(TTKIAError):
            client.upload_attachment(str(f), "conv-123")
 
        client.close()
 
    @pytest.mark.asyncio
    async def test_aupload_attachment(self, upload_response, tmp_path):
        """Versión async de upload_attachment."""
        f = tmp_path / "config.txt"
        f.write_text("interface lo\n")
 
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_response)
        client._http.post = AsyncMock(return_value=mock_resp)
 
        result = await client.aupload_attachment(str(f), "conv-123")
 
        assert result["name"] == "router_config.txt"
        assert result["status"] == "completed"
 
        await client.aclose()
 