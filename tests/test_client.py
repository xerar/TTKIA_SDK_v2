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
        """Regression: updated_at as float from MongoDB should not crash.

        El mock va sobre GET, no sobre POST: desde que las conversaciones se
        alinearon con el router REST, list_conversations() hace
        GET /conversations y devuelve la clave "conversations". Mientras el
        mock siguió puesto en POST, httpx intentaba resolver test.com de
        verdad y el test fallaba por DNS, no por lo que pretendía comprobar.
        """
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        list_data = {
            "conversations": [
                {
                    "conversation_id": "abc-123",
                    "title": "Test conv",
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": 1772621971.2031674,
                }
            ]
        }
        mock_resp = _mock_http_response(list_data)
        client._http_sync.get = MagicMock(return_value=mock_resp)

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
        # Con cabecera: esperar resuelve.
        assert exc_info.value.retryable is True
        client.close()

    def test_budget_exhausted_is_not_retryable(self):
        """429 sin Retry-After es un tope de gasto, no un rate limit.

        El backend manda la cabecera en el límite de frecuencia y NO la manda
        cuando se agota el presupuesto de la clave o del usuario. La ventana
        de gasto se mide en días, así que reintentar no levanta nada: solo
        cuesta una agregación sobre resources_usage por intento.
        """
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(
            {"detail": "API Key budget exhausted: $10.00 of $10.00"}, 429
        )
        mock_resp.headers = {}
        client._http_sync.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RateLimitError) as exc_info:
            client.query("test")
        assert exc_info.value.retryable is False
        client.close()

    def test_retry_after_http_date_stays_retryable(self):
        """Retry-After admite fecha HTTP además de segundos.

        No se adivina el valor —se cae al default— pero la presencia de la
        cabecera sigue significando que es un límite de frecuencia.
        """
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response({"detail": "Slow down"}, 429)
        mock_resp.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        client._http_sync.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RateLimitError) as exc_info:
            client.query("test")
        assert exc_info.value.retryable is True
        assert exc_info.value.retry_after == 60
        client.close()


# ═══════════════════════════════════════════════════════════
# ATTACHMENTS
# ═══════════════════════════════════════════════════════════

class TestAttachments:

    @pytest.fixture
    def upload_response_completed(self):
        """Respuesta de /chat-upload para imagen (completed inmediato)."""
        return {
            "path": "/data/attachments/testuser/conv-123/diagram.png",
            "name": "diagram.png",
            "size": 58,
            "type": "image/png",
            "stored_as": "physical_file",
            "status": "completed",
            "conversation_id": "conv-123",
        }

    @pytest.fixture
    def upload_response_processing(self):
        """Respuesta de /chat-upload para documento (processing en background)."""
        return {
            "path": "embedded://conv-123/router_config.txt",
            "name": "router_config.txt",
            "size": 512,
            "type": "text/plain",
            "stored_as": "vector_store",
            "status": "processing",
            "conversation_id": "conv-123",
        }

    def test_upload_image_returns_completed(self, upload_response_completed, tmp_path):
        """Las imágenes vuelven con status='completed' inmediatamente."""
        img = tmp_path / "diagram.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_response_completed)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        result = client.upload_attachment(str(img), "conv-123")

        assert result["name"] == "diagram.png"
        assert result["type"] == "image/png"
        assert result["status"] == "completed"
        assert client._http_sync.post.call_count == 1
        client.close()

    def test_upload_document_polls_until_completed(self, upload_response_processing, tmp_path):
        """Documentos en 'processing' deben pollear hasta 'completed'.

        El upload es POST /chat-upload; el poll es GET /conversations/{id}.
        Son dos verbos distintos y necesitan dos mocks distintos: con ambos
        en POST, el poll salía a la red real.
        """
        f = tmp_path / "router_config.txt"
        f.write_text("interface Gi0/0\n")

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")

        upload_resp = _mock_http_response(upload_response_processing)
        conv_processing = _mock_http_response({
            "conversation_id": "conv-123",
            "title": "Test",
            "messages": [],
            "file_attachments": [{"name": "router_config.txt", "status": "processing"}],
            "web_references": [],
        })
        conv_completed = _mock_http_response({
            "conversation_id": "conv-123",
            "title": "Test",
            "messages": [],
            "file_attachments": [{"name": "router_config.txt", "status": "completed"}],
            "web_references": [],
        })
        client._http_sync.post = MagicMock(return_value=upload_resp)
        client._http_sync.get = MagicMock(
            side_effect=[conv_processing, conv_completed]
        )

        result = client.upload_attachment(
            str(f), "conv-123",
            poll_interval=0.01,
            poll_timeout=5.0,
        )

        assert result["status"] == "completed"
        assert client._http_sync.post.call_count == 1   # el upload
        assert client._http_sync.get.call_count == 2    # processing -> completed
        client.close()

    def test_upload_skip_wait_returns_processing(self, upload_response_processing, tmp_path):
        """Con wait_for_embedding=False no se hace poll, devuelve 'processing'."""
        f = tmp_path / "router_config.txt"
        f.write_text("data")

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_response_processing)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        result = client.upload_attachment(
            str(f), "conv-123",
            wait_for_embedding=False,
        )

        assert result["status"] == "processing"
        assert client._http_sync.post.call_count == 1
        client.close()

    def test_query_with_attached_files(self, api_response):
        """query() pasa attached_files en el payload JSON."""
        attachment = {
            "path": "embedded://conv-123/config.txt",
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
            "path": "web://https://sec.cloudapps.cisco.com/...",
            "name": "url_abc123.txt",
            "type": "text/plain",
            "metadata": {"url": "https://sec.cloudapps.cisco.com/...", "title": "Cisco Advisory"},
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
        """Sin adjuntos, el payload manda listas vacías."""
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

    def test_index_url_returns_attachments(self):
        """index_url llama a /process-urls y devuelve attachments con path web://..."""
        url_attachments = [
            {
                "path": "web://https://sec.cloudapps.cisco.com/...",
                "name": "url_abc123.txt",
                "type": "text/plain",
                "metadata": {"url": "https://sec.cloudapps.cisco.com/...", "title": "Cisco Advisory"},
            }
        ]
        process_resp = {
            "query": "https://sec.cloudapps.cisco.com/...",
            "attachments": url_attachments,
            "security_errors": [],
            "conversation_id": "conv-123",
        }

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(process_resp)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        result = client.index_url("https://sec.cloudapps.cisco.com/...", "conv-123")

        assert len(result) == 1
        assert result[0]["path"].startswith("web://")
        # Verificar que se llamó a /process-urls
        call_args = client._http_sync.post.call_args
        assert "/process-urls" in str(call_args)
        client.close()

    def test_index_url_blocked_returns_empty(self):
        """URLs de dominios no permitidos devuelven lista vacía."""
        process_resp = {
            "query": "https://evil.com",
            "attachments": [],
            "security_errors": [{"type": "domain_blocked", "url": "https://evil.com"}],
            "conversation_id": "conv-123",
        }

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(process_resp)
        client._http_sync.post = MagicMock(return_value=mock_resp)

        result = client.index_url("https://evil.com", "conv-123")
        assert result == []
        client.close()

    @pytest.mark.asyncio
    async def test_aupload_attachment(self, upload_response_completed, tmp_path):
        """Versión async de upload_attachment para imagen."""
        img = tmp_path / "diagram.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(upload_response_completed)
        client._http.post = AsyncMock(return_value=mock_resp)

        result = await client.aupload_attachment(str(img), "conv-123")

        assert result["name"] == "diagram.png"
        assert result["status"] == "completed"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_aindex_url(self):
        """Versión async de index_url."""
        url_attachments = [
            {
                "path": "web://https://cisco.com/foo",
                "name": "url_xyz.txt",
                "type": "text/plain",
                "metadata": {"url": "https://cisco.com/foo", "title": "Foo"},
            }
        ]
        process_resp = {
            "query": "https://cisco.com/foo",
            "attachments": url_attachments,
            "security_errors": [],
            "conversation_id": "conv-123",
        }

        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        mock_resp = _mock_http_response(process_resp)
        client._http.post = AsyncMock(return_value=mock_resp)

        result = await client.aindex_url("https://cisco.com/foo", "conv-123")

        assert len(result) == 1
        assert result[0]["path"].startswith("web://")
        await client.aclose()

# ═══════════════════════════════════════════════════════════
# CONTRATO REAL DE /query_complete (backend 6.4.0 y 6.4.1)
# ═══════════════════════════════════════════════════════════

def _payload_640():
    """Respuesta tal como la emite el backend 6.4.0: metadatos crudos de Qdrant,
    `recommended_response` para la valoración, `links` como dicts (lo que
    llega de verdad aunque el backend lo declare List[str]) y webs con url."""
    return {
        "success": True,
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "query": "¿Qué modelos Catalyst soporta el servicio?",
        "response_text": (
            "Los modelos soportados son C8300 y C1111 [doc:1][doc:2]. "
            "Ver también la guía [link:1] y el aviso [web:1]. Repetida [doc:1]."
        ),
        "confidence": 0.9,
        "recommended_response": "La respuesta cita la tabla de modelos del DTS.",
        "query_extended": "¿Qué modelos Catalyst soporta el servicio?",
        "timing": {"preprocessing": 1.2, "finalize": 0.1},
        "token_counts": {"input": 1000, "output": 300},
        "inferred_environments": ["SCN_Tech"],
        "docs": [
            {"title": "01_DTS_Viptela.pdf", "source": "01_DTS_Viptela.pdf",
             "page": "43", "section_path": "Templates Configuration",
             "author": "Cisco", "tag": "Bookshelf", "_score": 0.8986,
             "chunk_index": 49, "chunk_type": "paragraph"},
            {"title": "00_DTS_Viptela.pdf", "source": "00_DTS_Viptela.pdf",
             "page": 12, "section_path": "5.1 Physical Devices", "_score": 0.8922},
        ],
        "links": [{"title": "FMC Admin Guide", "source": "Cisco",
                   "url": "https://www.cisco.com/fmc", "tag": "internet"}],
        "webs": [{"title": "Aviso PSIRT", "url": "https://sec.cloudapps.cisco.com/x",
                  "domain": "cisco.com", "description": "Advisory"}],
        "thinking_process": ["", "", "enhanced"],
        "mcp_tools": [{"name": "fmg_task_log", "status": "error",
                       "args": {}, "error": "Timeout calling MCP tool"}],
        "follow_ups": ["¿Y los ISR?"],
        "artifacts": [{"id": "a1", "version": 1, "title": "Cuadro"}],
        "attachments": [{"name": "informe.docx", "url": "/files/x"}],
        "error": None,
    }


class TestContract640:
    def test_parse_full_payload(self):
        r = TTKIAClient._parse_query_response(_payload_640())
        assert r.text.startswith("Los modelos")
        assert r.evaluation == "La respuesta cita la tabla de modelos del DTS."
        # Fuentes con todo su contexto
        d = r.docs[0]
        assert d.page == 43 and isinstance(d.page, int)
        assert d.section_path == "Templates Configuration"
        assert d.relevance == pytest.approx(0.8986)
        assert d.author == "Cisco"
        # links como Source, no como str
        assert r.links[0].title == "FMC Admin Guide"
        assert r.links[0].url == "https://www.cisco.com/fmc"
        # webs conservan la url
        assert r.webs[0].url.startswith("https://")
        assert r.webs[0].is_web
        assert not r.docs[0].is_web
        # campos nuevos
        assert r.artifacts[0]["id"] == "a1"
        assert r.attachments[0]["name"] == "informe.docx"
        assert r.mcp_tools[0].error == "Timeout calling MCP tool"
        # marcadores vacíos fuera
        assert r.thinking_process == ["enhanced"]
        assert len(r.sources) == 4

    def test_str_is_full_text_without_evaluation(self):
        r = TTKIAClient._parse_query_response(_payload_640())
        assert str(r) == r.text
        assert "%" not in str(r)[:6]
        assert r.summary().startswith("[90%]")

    def test_recommended_response_is_deprecated_alias(self):
        r = TTKIAClient._parse_query_response(_payload_640())
        with pytest.warns(DeprecationWarning):
            assert r.recommended_response == r.evaluation

    def test_citations_resolved_and_deduplicated(self):
        r = TTKIAClient._parse_query_response(_payload_640())
        markers = [c.marker for c in r.citations]
        assert markers == ["[doc:1]", "[doc:2]", "[link:1]", "[web:1]"]
        assert r.citations[0].source.page == 43
        assert [s.title for s in r.cited_sources] == [
            "01_DTS_Viptela.pdf", "00_DTS_Viptela.pdf", "FMC Admin Guide", "Aviso PSIRT"]

    def test_citation_out_of_range_has_no_source(self):
        p = _payload_640()
        p["response_text"] = "Dato [doc:9]."
        r = TTKIAClient._parse_query_response(p)
        assert r.citations[0].source is None
        assert r.cited_sources == []

    def test_text_plain(self):
        r = TTKIAClient._parse_query_response(_payload_640())
        assert "[doc:" not in r.text_plain
        assert "C1111 [1][2]." in r.text_plain

    def test_text_with_references(self):
        out = TTKIAClient._parse_query_response(_payload_640()).text_with_references()
        assert "## Referencias" in out
        assert "### Documentos" in out
        assert "[1] **01_DTS_Viptela.pdf** — *Cisco* — pág. 43, Templates Configuration" in out
        assert "### Enlaces internos" in out and "https://www.cisco.com/fmc" in out
        assert "### Web" in out and "https://sec.cloudapps.cisco.com/x" in out
        assert "[doc:" not in out

    def test_text_without_citations_has_no_references_section(self):
        p = _payload_640()
        p["response_text"] = "Sin citas."
        assert TTKIAClient._parse_query_response(p).text_with_references() == "Sin citas."

    def test_legacy_links_as_strings(self):
        p = _payload_640()
        p["links"] = ["https://legacy.example.com/page"]
        r = TTKIAClient._parse_query_response(p)
        assert r.links[0].source == "https://legacy.example.com/page"
        assert r.links[0].is_web

    def test_missing_optional_fields(self):
        r = TTKIAClient._parse_query_response({"success": True, "response_text": "ok"})
        assert r.text == "ok"
        assert r.evaluation is None
        assert r.docs == [] and r.links == [] and r.webs == []
        assert r.artifacts == [] and r.attachments == []
        assert r.citations == []

    def test_null_lists_from_backend(self):
        p = _payload_640()
        for k in ("docs", "links", "webs", "follow_ups", "artifacts", "attachments", "mcp_tools"):
            p[k] = None
        r = TTKIAClient._parse_query_response(p)
        assert r.sources == [] and r.follow_ups == [] and r.mcp_tools == []

    def test_bad_page_value(self):
        s = Source.model_validate({"title": "x", "page": "n/a", "section_path": None})
        assert s.page is None and s.section_path == ""


class TestContract641:
    def test_new_field_names(self):
        p = _payload_640()
        p.pop("recommended_response")
        p["evaluation"] = "Evaluación nueva"
        p["reasoning"] = "Primero miro el DTS..."
        r = TTKIAClient._parse_query_response(p)
        assert r.evaluation == "Evaluación nueva"
        assert r.reasoning == "Primero miro el DTS..."


class TestConversationMessageSources:
    def test_history_keeps_references(self):
        from ttkia_sdk.models import Conversation
        conv = Conversation(**{
            "conversation_id": "c1",
            "messages": [
                {"role": "human", "content": "q"},
                {"role": "assistant", "content": "a [doc:1]",
                 "recommended_response": "eval", "confidence": 0.8,
                 "docs": [{"title": "d.pdf", "page": 3, "_score": 0.9}],
                 "links": None, "webs": [{"title": "w", "url": "https://w"}]},
            ],
        })
        m = conv.assistant_messages[0]
        assert m.evaluation == "eval"
        assert m.docs[0].page == 3 and m.docs[0].relevance == 0.9
        assert m.links == []
        assert m.webs[0].is_web


class TestConversationMessageRobustness:
    def test_message_without_content(self):
        from ttkia_sdk.models import ConversationMessage
        assert ConversationMessage(role="assistant", content=None).content == ""
        assert ConversationMessage(role="assistant").content == ""


class TestCodeQueryNotices:
    def _client(self, data):
        client = TTKIAClient("https://test.com", api_key="ttkia_sk_test")
        client._http_sync.post = MagicMock(return_value=_mock_http_response(data))
        return client

    def test_notice_parsed(self):
        client = self._client({
            "success": True, "conversation_id": "s1", "message_id": "m1",
            "response_text": "ok", "token_counts": {"input": 10, "output": 5},
            "notices": [{"type": "model_notice", "reason": "refusal",
                         "from_model": "claude_sonnet_v5_5",
                         "to_model": "claude_sonnet_v5",
                         "message": "Sonnet 5.5 → Sonnet 5: declinó."}],
        })
        qr = client.code_query("x")
        assert len(qr.notices) == 1
        n = qr.notices[0]
        assert n.reason == "refusal" and n.to_model == "claude_sonnet_v5"
        assert str(n) == "Sonnet 5.5 → Sonnet 5: declinó."
        client.close()

    def test_backend_without_notices(self):
        client = self._client({
            "success": True, "conversation_id": "s1", "message_id": "m1",
            "response_text": "ok", "token_counts": {},
        })
        assert client.code_query("x").notices == []
        client.close()
