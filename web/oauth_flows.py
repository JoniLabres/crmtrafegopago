"""OAuth 2.0 flows for Meta Ads, Google Ads, LinkedIn Ads and TikTok Ads."""
import os
import secrets
import time
import httpx
from urllib.parse import urlencode

# In-memory fallback (works for single-process local dev)
_states: dict[str, tuple[str, float]] = {}  # token → (channel, expires_at)
_STATE_TTL = 600  # 10 minutes


def _db_state_save(token: str, channel: str) -> bool:
    """Persist OAuth state to PostgreSQL when available (required on Vercel)."""
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_states (
                    token TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                INSERT INTO oauth_states (token, channel, expires_at)
                VALUES (%s, %s, NOW() + INTERVAL '10 minutes')
                ON CONFLICT (token) DO NOTHING
            """, (token, channel))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _db_state_pop(token: str) -> str | None:
    """Consume and return channel for the given state token from DB."""
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        channel = None
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM oauth_states
                WHERE token = %s AND expires_at > NOW()
                RETURNING channel
            """, (token,))
            row = cur.fetchone()
            if row:
                channel = row[0]
        conn.commit()
        conn.close()
        return channel
    except Exception:
        return None


def generate_state(channel: str) -> str:
    token = secrets.token_urlsafe(24)
    # Try DB first (required on Vercel); fall back to memory
    if not _db_state_save(token, channel):
        _states[token] = (channel, time.time() + _STATE_TTL)
    return token


def verify_state(state: str, expected_channel: str) -> bool:
    # Try DB first
    channel = _db_state_pop(state)
    if channel is not None:
        return channel == expected_channel
    # Fall back to memory (local dev without DB)
    entry = _states.pop(state, None)
    if entry is None:
        return False
    channel, expires_at = entry
    if time.time() > expires_at:
        return False
    return channel == expected_channel


def _redirect_uri(base_url: str, channel: str) -> str:
    return f"{base_url}/auth/callback/{channel}"


# ── Meta Ads ─────────────────────────────────────────────────────────────────

def meta_auth_url(base_url: str) -> tuple[str, str]:
    state = generate_state("meta")
    params = {
        "client_id": os.getenv("META_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(base_url, "meta"),
        "scope": "ads_management,ads_read,business_management,email",
        "response_type": "code",
        "state": state,
    }
    return "https://www.facebook.com/v19.0/dialog/oauth?" + urlencode(params), state


async def meta_exchange(code: str, base_url: str) -> dict:
    async with httpx.AsyncClient() as client:
        # Short-lived token
        r = await client.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "client_id": os.getenv("META_CLIENT_ID"),
                "client_secret": os.getenv("META_CLIENT_SECRET"),
                "redirect_uri": _redirect_uri(base_url, "meta"),
                "code": code,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        short_token = data.get("access_token", "")

        # Exchange for long-lived token
        r2 = await client.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": os.getenv("META_CLIENT_ID"),
                "client_secret": os.getenv("META_CLIENT_SECRET"),
                "fb_exchange_token": short_token,
            },
            timeout=15,
        )
        r2.raise_for_status()
        long_data = r2.json()
        return {"access_token": long_data.get("access_token", short_token)}


async def meta_list_accounts(access_token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://graph.facebook.com/v19.0/me/adaccounts",
            params={
                "access_token": access_token,
                "fields": "id,name,account_status,currency",
                "limit": 50,
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("data", [])


# ── Google Ads ────────────────────────────────────────────────────────────────

def google_auth_url(base_url: str) -> tuple[str, str]:
    state = generate_state("google")
    params = {
        "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(base_url, "google"),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/adwords",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), state


async def google_exchange(code: str, base_url: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
                "redirect_uri": _redirect_uri(base_url, "google"),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


async def google_list_accounts(refresh_token: str) -> list[dict]:
    """List accessible Google Ads customer IDs via the REST API."""
    async with httpx.AsyncClient() as client:
        # Get fresh access token
        tr = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        tr.raise_for_status()
        access_token = tr.json().get("access_token", "")

        r = await client.get(
            "https://googleads.googleapis.com/v17/customers:listAccessibleCustomers",
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            },
            timeout=15,
        )
        if r.status_code != 200:
            return []
        # Returns resource names like "customers/1234567890"
        names = r.json().get("resourceNames", [])
        return [{"id": n.split("/")[-1], "name": n.split("/")[-1]} for n in names]


# ── LinkedIn Ads ──────────────────────────────────────────────────────────────

def linkedin_auth_url(base_url: str) -> tuple[str, str]:
    state = generate_state("linkedin")
    params = {
        "response_type": "code",
        "client_id": os.getenv("LINKEDIN_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(base_url, "linkedin"),
        "state": state,
        "scope": "r_ads rw_ads r_ads_reporting",
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(params), state


async def linkedin_exchange(code: str, base_url: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(base_url, "linkedin"),
                "client_id": os.getenv("LINKEDIN_CLIENT_ID"),
                "client_secret": os.getenv("LINKEDIN_CLIENT_SECRET"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


async def linkedin_list_accounts(access_token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.linkedin.com/v2/adAccountsV2",
            params={"q": "search", "search.status.values[0]": "ACTIVE"},
            headers={"Authorization": f"Bearer {access_token}", "LinkedIn-Version": "202401"},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        elements = r.json().get("elements", [])
        return [{"id": str(e.get("id", "")), "name": e.get("name", str(e.get("id", "")))} for e in elements]


# ── TikTok Ads ────────────────────────────────────────────────────────────────

def tiktok_auth_url(base_url: str) -> tuple[str, str]:
    state = generate_state("tiktok")
    params = {
        "app_id": os.getenv("TIKTOK_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(base_url, "tiktok"),
        "state": state,
        "scope": "advertiser.read,advertiser.manage",
        "response_type": "code",
    }
    return "https://business-api.tiktok.com/portal/auth?" + urlencode(params), state


async def tiktok_exchange(code: str, base_url: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/",
            json={
                "app_id": os.getenv("TIKTOK_CLIENT_ID"),
                "secret": os.getenv("TIKTOK_CLIENT_SECRET"),
                "auth_code": code,
            },
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        return d.get("data", {})


async def tiktok_list_accounts(access_token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://business-api.tiktok.com/open_api/v1.3/oauth2/advertiser/get/",
            params={"app_id": os.getenv("TIKTOK_CLIENT_ID"), "secret": os.getenv("TIKTOK_CLIENT_SECRET")},
            headers={"Access-Token": access_token},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json().get("data", {})
        return [{"id": str(a.get("advertiser_id", "")), "name": a.get("advertiser_name", "")} for a in data.get("list", [])]
