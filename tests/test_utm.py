import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracking.utm_builder import build_utm, build_campaign_slug
from tracking.utm_validator import validate_utm


# ── utm_builder ──────────────────────────────────────────────────────────────

class TestBuildUTM:
    def test_caso_feliz(self):
        url = build_utm(
            "https://site.com/lp",
            source="meta",
            medium="paid_social",
            campaign_parts={"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            content="video_dor_v1",
            term="lookalike-clientes",
        )
        assert "utm_source=meta" in url
        assert "utm_medium=paid_social" in url
        assert "utm_campaign=produto-a_topo_leads_2025-05" in url
        assert "utm_content=video_dor_v1" in url
        assert "utm_term=lookalike-clientes" in url

    def test_source_invalido(self):
        with pytest.raises(ValueError, match="utm_source"):
            build_utm(
                "https://site.com",
                source="twitter",
                medium="paid_social",
                campaign_parts={"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            )

    def test_medium_invalido(self):
        with pytest.raises(ValueError, match="utm_medium"):
            build_utm(
                "https://site.com",
                source="meta",
                medium="organic",
                campaign_parts={"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            )

    def test_funil_invalido(self):
        with pytest.raises(ValueError, match="funil"):
            build_utm(
                "https://site.com",
                source="meta",
                medium="paid_social",
                campaign_parts={"produto": "produto-a", "funil": "base", "objetivo": "leads", "ano_mes": "2025-05"},
            )

    def test_campo_faltando(self):
        with pytest.raises(ValueError, match="campaign_parts faltando campos"):
            build_utm(
                "https://site.com",
                source="meta",
                medium="paid_social",
                campaign_parts={"produto": "produto-a", "funil": "topo"},
            )

    def test_ano_mes_formato_invalido(self):
        with pytest.raises(ValueError, match="ano_mes"):
            build_utm(
                "https://site.com",
                source="meta",
                medium="paid_social",
                campaign_parts={"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "05-2025"},
            )

    def test_sem_content_e_term(self):
        url = build_utm(
            "https://site.com",
            source="google",
            medium="paid_search",
            campaign_parts={"produto": "produto-b", "funil": "fundo", "objetivo": "vendas", "ano_mes": "2025-06"},
        )
        assert "utm_content" not in url
        assert "utm_term" not in url

    def test_normalizacao_acentos(self):
        url = build_utm(
            "https://site.com",
            source="meta",
            medium="paid_social",
            campaign_parts={"produto": "Produto Ação", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
        )
        assert "produto-acao" in url


# ── utm_validator ─────────────────────────────────────────────────────────────

class TestValidateUTM:
    def test_utm_valida(self):
        url = (
            "https://site.com?utm_source=meta&utm_medium=paid_social"
            "&utm_campaign=produto-a_topo_leads_2025-05"
            "&utm_content=video_dor_v1&utm_term=lookalike-clientes"
        )
        result = validate_utm(url)
        assert result["status"] == "ok"
        assert result["erros"] == []

    def test_source_errado(self):
        url = (
            "https://site.com?utm_source=instagram&utm_medium=paid_social"
            "&utm_campaign=produto-a_topo_leads_2025-05"
            "&utm_content=video_dor_v1&utm_term=lookalike-clientes"
        )
        result = validate_utm(url)
        assert result["status"] == "error"
        assert any("utm_source" in e for e in result["erros"])

    def test_campaign_mal_formatado(self):
        url = (
            "https://site.com?utm_source=meta&utm_medium=paid_social"
            "&utm_campaign=produto-a_topo_2025-05"
            "&utm_content=video_dor_v1&utm_term=lookalike-clientes"
        )
        result = validate_utm(url)
        assert result["status"] == "error"
        assert any("utm_campaign" in e for e in result["erros"])

    def test_parametro_ausente(self):
        url = "https://site.com?utm_source=meta&utm_medium=paid_social"
        result = validate_utm(url)
        assert result["status"] == "error"
        assert any("utm_campaign" in e for e in result["erros"])

    def test_maiuscula_rejeitada(self):
        url = (
            "https://site.com?utm_source=Meta&utm_medium=paid_social"
            "&utm_campaign=produto-a_topo_leads_2025-05"
            "&utm_content=video_dor_v1&utm_term=lookalike-clientes"
        )
        result = validate_utm(url)
        assert result["status"] == "error"
        assert any("maiúscula" in e for e in result["erros"])

    def test_objetivo_invalido_em_campaign(self):
        url = (
            "https://site.com?utm_source=meta&utm_medium=paid_social"
            "&utm_campaign=produto-a_topo_branding_2025-05"
            "&utm_content=video_dor_v1&utm_term=lookalike-clientes"
        )
        result = validate_utm(url)
        assert result["status"] == "error"
        assert any("objetivo" in e for e in result["erros"])
