import os
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def check(label: str, fn) -> bool:
    try:
        fn()
        print(f"  OK  {label}")
        return True
    except Exception as e:
        msg = str(e)[:80]
        print(f"  FALHA  {label}: {msg}")
        return False


def check_env(vars: list) -> bool:
    missing = [v for v in vars if not os.getenv(v)]
    if missing:
        print(f"  AVISO  Variáveis não configuradas: {', '.join(missing)}")
        return False
    print(f"  OK  Variáveis de ambiente: {', '.join(vars)}")
    return True


# ── Variáveis de ambiente ─────────────────────────────────────────────────────

def section_env():
    print("\n[1/6] VARIÁVEIS DE AMBIENTE")
    groups = {
        "GTM": ["GTM_ACCOUNT_ID", "GTM_CONTAINER_ID"],
        "GA4": ["GA4_MEASUREMENT_ID", "GA4_API_SECRET"],
        "Meta Ads": ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"],
        "Google Ads": ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CUSTOMER_ID"],
        "LinkedIn Ads": ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AD_ACCOUNT_ID"],
        "TikTok Ads": ["TIKTOK_ACCESS_TOKEN", "TIKTOK_AD_ACCOUNT_ID"],
        "HubSpot": ["HUBSPOT_API_KEY"],
        "Claude API": ["ANTHROPIC_API_KEY"],
        "Slack": ["SLACK_WEBHOOK_URL"],
        "PostgreSQL": ["DATABASE_URL"],
    }
    results = {name: check_env(vars) for name, vars in groups.items()}
    return results


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def section_database():
    print("\n[2/6] POSTGRESQL")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("  AVISO  DATABASE_URL não configurada — pulando")
        return False

    def test_db():
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()

    return check("Conexão PostgreSQL", test_db)


# ── HubSpot ───────────────────────────────────────────────────────────────────

def section_hubspot():
    print("\n[3/6] HUBSPOT")
    key = os.getenv("HUBSPOT_API_KEY")
    if not key:
        print("  AVISO  HUBSPOT_API_KEY não configurada — pulando")
        return False

    def test_hubspot():
        sys.path.insert(0, str(Path(__file__).parent.parent / "hubspot"))
        from hubspot_client import HubSpotClient
        client = HubSpotClient()
        client.get("/crm/v3/properties/contacts?limit=1")

    return check("HubSpot API", test_hubspot)


# ── Claude API ────────────────────────────────────────────────────────────────

def section_claude():
    print("\n[4/6] CLAUDE API (ANTHROPIC)")
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("  AVISO  ANTHROPIC_API_KEY não configurada — pulando")
        return False

    def test_claude():
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        assert resp.content

    return check("Claude API (ping)", test_claude)


# ── Slack ─────────────────────────────────────────────────────────────────────

def section_slack():
    print("\n[5/6] SLACK")
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook:
        print("  AVISO  SLACK_WEBHOOK_URL não configurada — pulando")
        return False

    def test_slack():
        import requests
        resp = requests.post(webhook, json={"text": "health-check: trafego-pago OK"}, timeout=10)
        resp.raise_for_status()

    return check("Slack Webhook", test_slack)


# ── Canais de Ads ─────────────────────────────────────────────────────────────

def section_ads():
    print("\n[6/6] CANAIS DE ADS")
    results = {}

    if os.getenv("META_ACCESS_TOKEN") and os.getenv("META_AD_ACCOUNT_ID"):
        def test_meta():
            import requests
            token = os.getenv("META_ACCESS_TOKEN")
            account = os.getenv("META_AD_ACCOUNT_ID")
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{account}",
                params={"fields": "name", "access_token": token},
                timeout=10,
            )
            resp.raise_for_status()
            assert "name" in resp.json()
        results["Meta Ads"] = check("Meta Ads API", test_meta)
    else:
        print("  AVISO  Meta Ads — credenciais não configuradas")
        results["Meta Ads"] = False

    if os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"):
        def test_google():
            import importlib
            spec = importlib.util.find_spec("google.ads.googleads")
            if spec is None:
                raise ImportError("google-ads SDK não instalado")
        results["Google Ads"] = check("Google Ads SDK", test_google)
    else:
        print("  AVISO  Google Ads — credenciais não configuradas")
        results["Google Ads"] = False

    if os.getenv("LINKEDIN_ACCESS_TOKEN"):
        def test_linkedin():
            import requests
            resp = requests.get(
                "https://api.linkedin.com/v2/me",
                headers={"Authorization": f"Bearer {os.getenv('LINKEDIN_ACCESS_TOKEN')}"},
                timeout=10,
            )
            resp.raise_for_status()
        results["LinkedIn Ads"] = check("LinkedIn Ads API", test_linkedin)
    else:
        print("  AVISO  LinkedIn Ads — credenciais não configuradas")
        results["LinkedIn Ads"] = False

    if os.getenv("TIKTOK_ACCESS_TOKEN"):
        def test_tiktok():
            import requests
            resp = requests.get(
                "https://business-api.tiktok.com/open_api/v1.3/user/info/",
                headers={"Access-Token": os.getenv("TIKTOK_ACCESS_TOKEN")},
                timeout=10,
            )
            resp.raise_for_status()
        results["TikTok Ads"] = check("TikTok Ads API", test_tiktok)
    else:
        print("  AVISO  TikTok Ads — credenciais não configuradas")
        results["TikTok Ads"] = False

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  HEALTH CHECK — trafego-pago")
    print("=" * 55)

    env = section_env()
    db = section_database()
    hs = section_hubspot()
    claude = section_claude()
    slack = section_slack()
    ads = section_ads()

    all_results = [db, hs, claude, slack] + list(ads.values())
    ok = sum(1 for r in all_results if r)
    total = len(all_results)

    print("\n" + "=" * 55)
    print(f"  RESULTADO: {ok}/{total} serviços OK")
    not_ok = total - ok
    if not_ok == 0:
        print("  Status: TUDO VERDE")
    elif not_ok <= 2:
        print("  Status: PARCIALMENTE OK (configure as variáveis ausentes)")
    else:
        print("  Status: ATENÇÃO — vários serviços indisponíveis")
    print("=" * 55)


if __name__ == "__main__":
    main()
