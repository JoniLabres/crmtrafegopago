import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "hubspot"))

from hubspot_client import HubSpotClient
from create_properties import create_properties, CONTACT_PROPERTIES, DEAL_PROPERTIES


# ── HubSpotClient ─────────────────────────────────────────────────────────────

class TestHubSpotClient:
    def test_inicializa_com_api_key(self):
        client = HubSpotClient(api_key="test-key-123")
        assert client.api_key == "test-key-123"

    def test_erro_sem_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("hubspot_client.os.getenv", return_value=None):
                with pytest.raises(ValueError, match="HUBSPOT_API_KEY"):
                    HubSpotClient()

    def test_headers_de_auth(self):
        client = HubSpotClient(api_key="test-key-123")
        assert "Authorization" in client.session.headers
        assert "Bearer test-key-123" in client.session.headers["Authorization"]

    @patch("hubspot_client.requests.Session.request")
    def test_get_sucesso(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"id": "123"}'
        mock_response.json.return_value = {"id": "123"}
        mock_request.return_value = mock_response

        client = HubSpotClient(api_key="test-key")
        result = client.get("/crm/v3/properties/contacts/utm_source")
        assert result == {"id": "123"}

    @patch("hubspot_client.requests.Session.request")
    @patch("hubspot_client.time.sleep")
    def test_retry_em_rate_limit_429(self, mock_sleep, mock_request):
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "1"}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"ok": true}'
        success_response.json.return_value = {"ok": True}

        mock_request.side_effect = [rate_limit_response, success_response]

        client = HubSpotClient(api_key="test-key")
        result = client.get("/test")
        assert result == {"ok": True}
        mock_sleep.assert_called_once_with(1)

    @patch("hubspot_client.requests.Session.request")
    def test_erro_401_levanta_permission_error(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_request.return_value = mock_response

        client = HubSpotClient(api_key="test-key")
        with pytest.raises(PermissionError, match="401"):
            client.get("/test")

    @patch("hubspot_client.requests.Session.request")
    def test_post_envia_json(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"name": "utm_source"}'
        mock_response.json.return_value = {"name": "utm_source"}
        mock_request.return_value = mock_response

        client = HubSpotClient(api_key="test-key")
        result = client.post("/crm/v3/properties/contacts", json={"name": "utm_source"})
        assert result["name"] == "utm_source"


# ── create_properties ─────────────────────────────────────────────────────────

class TestCreateProperties:
    def _make_client(self):
        return HubSpotClient(api_key="test-key")

    @patch("create_properties._property_exists", return_value=False)
    def test_cria_propriedade_nova(self, mock_exists):
        client = self._make_client()
        client.post = MagicMock(return_value={"name": "utm_source"})

        props = [CONTACT_PROPERTIES[0]]
        result = create_properties(client, "contacts", props)

        assert result["criadas"] == 1
        assert result["ja_existiam"] == 0
        client.post.assert_called_once()

    @patch("create_properties._property_exists", return_value=True)
    def test_idempotencia_propriedade_existente(self, mock_exists):
        client = self._make_client()
        client.post = MagicMock()

        props = [CONTACT_PROPERTIES[0]]
        result = create_properties(client, "contacts", props)

        assert result["criadas"] == 0
        assert result["ja_existiam"] == 1
        client.post.assert_not_called()

    @patch("create_properties._property_exists")
    def test_cria_apenas_novas(self, mock_exists):
        mock_exists.side_effect = [True, False, True]
        client = self._make_client()
        client.post = MagicMock(return_value={"name": "test"})

        props = CONTACT_PROPERTIES[:3]
        result = create_properties(client, "contacts", props)

        assert result["criadas"] == 1
        assert result["ja_existiam"] == 2

    def test_contact_properties_tem_campos_obrigatorios(self):
        required = {"name", "label", "type", "fieldType", "groupName"}
        for prop in CONTACT_PROPERTIES:
            assert required.issubset(prop.keys()), f"Propriedade sem campos: {prop['name']}"

    def test_deal_properties_tem_campos_obrigatorios(self):
        required = {"name", "label", "type", "fieldType", "groupName"}
        for prop in DEAL_PROPERTIES:
            assert required.issubset(prop.keys()), f"Propriedade sem campos: {prop['name']}"

    def test_total_contact_properties(self):
        assert len(CONTACT_PROPERTIES) == 7

    def test_total_deal_properties(self):
        assert len(DEAL_PROPERTIES) == 5
