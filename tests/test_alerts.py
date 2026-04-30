import pytest
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts import AlertSystem


def make_system(slack_webhook="https://hooks.slack.com/test"):
    system = AlertSystem(db_conn=MagicMock(), slack_webhook=slack_webhook)
    return system


# ── check_cpl_alto ────────────────────────────────────────────────────────────

class TestCplAlto:
    def test_dispara_quando_cpl_acima_da_meta(self):
        system = make_system()
        data = [{"campaign_utm": "produto-a_topo_leads_2025-05", "channel": "meta", "cpl_medio": 145.0, "dias_acima": 3}]
        alerts = system.check_cpl_alto("produto-a", data=data)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "cpl_alto"
        assert alerts[0]["severity"] == "atencao"
        assert "145" in alerts[0]["message"]

    def test_nao_dispara_com_lista_vazia(self):
        system = make_system()
        alerts = system.check_cpl_alto("produto-a", data=[])
        assert alerts == []

    def test_multiplos_alertas(self):
        system = make_system()
        data = [
            {"campaign_utm": "camp-1", "channel": "meta", "cpl_medio": 150.0, "dias_acima": 4},
            {"campaign_utm": "camp-2", "channel": "google", "cpl_medio": 160.0, "dias_acima": 5},
        ]
        alerts = system.check_cpl_alto("produto-a", data=data)
        assert len(alerts) == 2


# ── check_roas_baixo ──────────────────────────────────────────────────────────

class TestRoasBaixo:
    def test_dispara_quando_roas_abaixo_da_meta(self):
        system = make_system()
        data = [{"campaign_utm": "produto-a_fundo_vendas_2025-05", "channel": "google", "roas_medio": 2.1, "dias_abaixo": 7}]
        alerts = system.check_roas_baixo("produto-a", data=data)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "roas_baixo"
        assert "2.1" in alerts[0]["message"]

    def test_nao_dispara_com_lista_vazia(self):
        system = make_system()
        alerts = system.check_roas_baixo("produto-a", data=[])
        assert alerts == []


# ── check_queda_leads ─────────────────────────────────────────────────────────

class TestQuedaLeads:
    def test_dispara_severo_quando_queda_maior_70pct(self):
        system = make_system()
        data = [{"campaign_utm": "camp-1", "channel": "meta", "leads_hoje": 2, "leads_media_7d": 15.0, "queda_pct": 86.7}]
        alerts = system.check_queda_leads("produto-a", data=data)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critico"
        assert alerts[0]["alert_type"] == "queda_leads"

    def test_dispara_atencao_quando_queda_entre_50_e_70pct(self):
        system = make_system()
        data = [{"campaign_utm": "camp-1", "channel": "meta", "leads_hoje": 4, "leads_media_7d": 10.0, "queda_pct": 60.0}]
        alerts = system.check_queda_leads("produto-a", data=data)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "atencao"

    def test_nao_dispara_sem_queda(self):
        system = make_system()
        alerts = system.check_queda_leads("produto-a", data=[])
        assert alerts == []


# ── check_budget_pace ─────────────────────────────────────────────────────────

class TestBudgetPace:
    def test_dispara_quando_pace_acima_110pct(self):
        system = make_system()
        data = [{"campaign_utm": "camp-1", "channel": "meta", "gasto_acumulado": 2800.0, "budget_proporcional": 2500.0, "pace_pct": 112.0}]
        alerts = system.check_budget_pace("produto-a", data=data)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "budget_pace"
        assert "112" in alerts[0]["message"]

    def test_nao_dispara_com_lista_vazia(self):
        system = make_system()
        alerts = system.check_budget_pace("produto-a", data=[])
        assert alerts == []


# ── send_slack ────────────────────────────────────────────────────────────────

class TestSendSlack:
    @patch("alerts.requests.post")
    def test_envia_mensagem_com_emoji_correto(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        system = make_system()
        result = system.send_slack("#alertas-midia", "CPL alto detectado", "atencao")
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert "🟡" in payload["text"]
        assert "ATENCAO" in payload["text"]

    @patch("alerts.requests.post")
    def test_envia_critico_com_emoji_vermelho(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        system = make_system()
        result = system.send_slack("#alertas-critico", "Queda de leads", "critico")
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert "🔴" in payload["text"]

    def test_retorna_false_sem_webhook(self):
        system = AlertSystem(db_conn=MagicMock(), slack_webhook=None)
        result = system.send_slack("#alertas-midia", "teste", "atencao")
        assert result is False

    @patch("alerts.requests.post")
    def test_nao_envia_mensagem_real_nos_testes(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        system = make_system()
        system.send_slack("#alertas-midia", "teste de mock", "ok")
        mock_post.assert_called_once()
        args = mock_post.call_args
        assert "hooks.slack.com/test" in args.args[0]


# ── check_all ─────────────────────────────────────────────────────────────────

class TestCheckAll:
    def test_check_all_agrega_todos_alertas(self):
        system = make_system()
        system.check_cpl_alto = MagicMock(return_value=[{"alert_type": "cpl_alto", "severity": "atencao", "message": "CPL alto", "channel": "meta", "campaign_utm": "camp", "produto": "produto-a", "details": {}}])
        system.check_roas_baixo = MagicMock(return_value=[])
        system.check_queda_leads = MagicMock(return_value=[])
        system.check_budget_pace = MagicMock(return_value=[])
        system.send_slack = MagicMock(return_value=True)
        system.log_alert = MagicMock()

        alerts = system.check_all()
        assert len(alerts) == len(system.thresholds)
        system.send_slack.assert_called()
        system.log_alert.assert_called()
