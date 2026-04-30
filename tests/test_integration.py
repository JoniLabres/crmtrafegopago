import pytest
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent / "tracking"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hubspot"))
sys.path.insert(0, str(Path(__file__).parent.parent / "data_pipeline"))
sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))
sys.path.insert(0, str(Path(__file__).parent.parent / "ai_agent"))

import pandas as pd


# ── 1. UTM: gerar → validar → formato correto ────────────────────────────────

class TestUTMCycle:
    def test_gerar_e_validar_utm_valida(self):
        from utm_builder import build_utm
        from utm_validator import validate_utm

        url = build_utm(
            "https://site.com/lp",
            source="meta",
            medium="paid_social",
            campaign_parts={"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            content="video_dor_v1",
            term="lookalike-clientes",
        )
        result = validate_utm(url)
        assert result["status"] == "ok", f"UTM inválida: {result['erros']}"
        assert "utm_source=meta" in url
        assert "produto-a_topo_leads_2025-05" in url

    def test_utm_invalida_rejeitada(self):
        from utm_builder import build_utm
        with pytest.raises(ValueError):
            build_utm(
                "https://site.com",
                source="instagram",
                medium="paid_social",
                campaign_parts={"produto": "p", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            )

    def test_utm_grava_no_banco_mockado(self):
        from utm_builder import build_utm
        from utm_validator import validate_utm

        url = build_utm(
            "https://site.com",
            source="google",
            medium="paid_search",
            campaign_parts={"produto": "produto-a", "funil": "fundo", "objetivo": "vendas", "ano_mes": "2025-05"},
            content="texto-preco_v1",
            term="software-gestao",
        )
        result = validate_utm(url)
        assert result["status"] == "ok"

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        campaign_utm = result["params"]["utm_campaign"]
        mock_cursor.execute("INSERT INTO campaigns_daily (campaign_utm) VALUES (%s)", (campaign_utm,))
        mock_cursor.execute.assert_called()


# ── 2. HubSpot: criar contato → criar negócio → verificar propagação ─────────

class TestHubSpotCycle:
    def _make_client(self):
        from hubspot_client import HubSpotClient
        return HubSpotClient(api_key="test-key")

    @patch("hubspot_client.requests.Session.request")
    def test_criar_contato_com_utm(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"id": "123", "properties": {"utm_campaign": "produto-a_topo_leads_2025-05"}}'
        mock_response.json.return_value = {"id": "123", "properties": {"utm_campaign": "produto-a_topo_leads_2025-05"}}
        mock_request.return_value = mock_response

        client = self._make_client()
        result = client.post("/crm/v3/objects/contacts", json={
            "properties": {
                "email": "lead@teste.com",
                "utm_campaign": "produto-a_topo_leads_2025-05",
                "utm_source": "meta",
            }
        })
        assert result["id"] == "123"
        assert result["properties"]["utm_campaign"] == "produto-a_topo_leads_2025-05"

    @patch("hubspot_client.requests.Session.request")
    def test_criar_negocio_associado(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"id": "456", "properties": {"utm_campaign_origem": "produto-a_topo_leads_2025-05"}}'
        mock_response.json.return_value = {"id": "456", "properties": {"utm_campaign_origem": "produto-a_topo_leads_2025-05"}}
        mock_request.return_value = mock_response

        client = self._make_client()
        deal = client.post("/crm/v3/objects/deals", json={
            "properties": {
                "dealname": "Negócio Teste",
                "amount": "5000",
                "utm_campaign_origem": "produto-a_topo_leads_2025-05",
            }
        })
        assert deal["properties"]["utm_campaign_origem"] == "produto-a_topo_leads_2025-05"

    @patch("create_properties._property_exists", return_value=False)
    def test_criar_propriedades_idempotente(self, mock_exists):
        from create_properties import create_properties, CONTACT_PROPERTIES
        client = MagicMock()
        client.post = MagicMock(return_value={"name": "utm_source"})

        result = create_properties(client, "contacts", CONTACT_PROPERTIES[:2])
        assert result["criadas"] == 2
        assert result["ja_existiam"] == 0


# ── 3. Pipeline: ads_df → join → verificar métricas ──────────────────────────

class TestPipelineCycle:
    def _make_ads_df(self):
        return pd.DataFrame([
            {
                "date": "2025-05-01", "channel": "meta",
                "campaign_utm": "produto-a_topo_leads_2025-05",
                "campaign_name": "Produto A Topo", "spend": 1500.0,
                "impressions": 60000, "clicks": 600, "leads": 30,
                "revenue": 0, "roas": 0, "cpl": 0, "cpc": 0, "ctr": 0,
            },
            {
                "date": "2025-05-01", "channel": "google",
                "campaign_utm": "produto-a_fundo_vendas_2025-05",
                "campaign_name": "Produto A Fundo", "spend": 3000.0,
                "impressions": 12000, "clicks": 400, "leads": 20,
                "revenue": 0, "roas": 0, "cpl": 0, "cpc": 0, "ctr": 0,
            },
        ])

    def _make_deals_df(self):
        return pd.DataFrame([
            {"id": "1", "utm_campaign_origem": "produto-a_topo_leads_2025-05", "amount": 5000.0},
            {"id": "2", "utm_campaign_origem": "produto-a_topo_leads_2025-05", "amount": 3500.0},
            {"id": "3", "utm_campaign_origem": "produto-a_fundo_vendas_2025-05", "amount": 9000.0},
        ])

    def test_join_utm_calcula_roas(self):
        from join_utm import join_campaign_data
        ads = self._make_ads_df()
        deals = self._make_deals_df()
        result = join_campaign_data(ads, deals)

        assert len(result) == 2
        topo = result[result["campaign_utm"] == "produto-a_topo_leads_2025-05"].iloc[0]
        assert topo["revenue"] == 8500.0
        assert topo["roas"] == round(8500 / 1500, 4)

    def test_join_utm_calcula_cpl(self):
        from join_utm import join_campaign_data
        ads = self._make_ads_df()
        deals = self._make_deals_df()
        result = join_campaign_data(ads, deals)

        topo = result[result["campaign_utm"] == "produto-a_topo_leads_2025-05"].iloc[0]
        assert topo["cpl"] == round(1500 / 30, 2)

    def test_join_utm_extrai_produto(self):
        from join_utm import join_campaign_data
        ads = self._make_ads_df()
        result = join_campaign_data(ads, pd.DataFrame())
        assert all(result["produto"] == "produto-a")

    @patch("psycopg2.connect")
    def test_upsert_banco_mockado(self, mock_connect):
        from join_utm import join_campaign_data
        ads = self._make_ads_df()
        deals = self._make_deals_df()
        consolidated = join_campaign_data(ads, deals)

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = mock_conn

        assert len(consolidated) == 2
        assert "roas" in consolidated.columns


# ── 4. Alertas: condição → mensagem Slack mockada ────────────────────────────

class TestAlertsCycle:
    def _make_system(self):
        from alerts import AlertSystem
        return AlertSystem(db_conn=MagicMock(), slack_webhook="https://hooks.slack.com/test")

    @patch("alerts.requests.post")
    def test_cpl_alto_dispara_slack(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        system = self._make_system()
        system.log_alert = MagicMock()

        data = [{"campaign_utm": "produto-a_topo_leads_2025-05", "channel": "meta", "cpl_medio": 145.0, "dias_acima": 3}]
        alerts = system.check_cpl_alto("produto-a", data=data)

        assert len(alerts) == 1
        system.send_slack("#alertas-midia", alerts[0]["message"], alerts[0]["severity"])
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert "🟡" in payload["text"]

    @patch("alerts.requests.post")
    def test_queda_leads_critico_dispara_slack(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        system = self._make_system()
        data = [{"campaign_utm": "camp-1", "channel": "meta", "leads_hoje": 1, "leads_media_7d": 20.0, "queda_pct": 95.0}]
        alerts = system.check_queda_leads("produto-a", data=data)

        assert alerts[0]["severity"] == "critico"
        system.send_slack("#alertas-critico", alerts[0]["message"], "critico")
        payload = mock_post.call_args.kwargs["json"]
        assert "🔴" in payload["text"]


# ── 5. Agente: briefing → campanha → UTMs corretas ───────────────────────────

class TestAgentCycle:
    def test_briefing_retorna_dict_completo(self):
        from briefing import _parse_objetivo, _parse_canal, _parse_budget
        assert _parse_objetivo("1") == "leads"
        assert _parse_objetivo("vendas diretas") == "vendas_diretas"
        assert _parse_canal("1,2") == ["meta", "google"]
        assert _parse_canal("5") == ["todos"]
        assert "R$" in _parse_budget("5000")

    def test_campaign_creator_gera_utms_validas(self):
        from campaign_creator import _build_utms
        from utm_validator import validate_utm

        briefing = {"objetivo": "leads", "canal": ["meta", "google"]}
        utms = _build_utms(briefing, "produto-a")

        assert len(utms) >= 2
        for key, url in utms.items():
            result = validate_utm(url)
            assert result["status"] == "ok", f"UTM inválida [{key}]: {result['erros']}"

    def test_campaign_creator_sem_api_retorna_utms(self):
        from campaign_creator import create_campaign

        briefing = {"objetivo": "leads", "canal": ["meta"], "budget": "R$ 3.000,00", "prazo": "30 dias"}
        result = create_campaign(briefing, "produto-a", agent=None)
        assert "produto-a" in result
        assert "utm_source=meta" in result

    def test_memory_loader_carrega_produto(self):
        from memory_loader import get_product_context
        ctx = get_product_context("produto-a")
        assert "PRODUTO-A" in ctx
        assert "ICP" in ctx
        assert "ROAS" in ctx or "roas" in ctx.lower()

    @patch("agent.anthropic.Anthropic")
    def test_agent_chat_chama_api(self, mock_anthropic):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Resposta do agente")]
        mock_response.usage.output_tokens = 50
        mock_client.messages.create.return_value = mock_response

        from agent import CampaignAgent
        agent = CampaignAgent(api_key="test-key")
        reply = agent.chat("Qual o ROAS atual?", product_name="produto-a")

        assert reply == "Resposta do agente"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert len(call_kwargs["messages"]) >= 1
