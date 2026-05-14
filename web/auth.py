"""
auth.py — Autenticação com sessões assinadas em PostgreSQL.
Sem dependências externas: usa apenas stdlib (hashlib, secrets, hmac).
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

SESSION_DAYS = 30

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS auth_users (
    id            SERIAL      PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    name          VARCHAR(255),
    role          VARCHAR(20)  NOT NULL DEFAULT 'user'
                  CHECK (role IN ('admin','user')),
    password_hash TEXT         NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token      VARCHAR(64)  PRIMARY KEY,
    user_id    INTEGER      NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ  NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token);
"""


def _conn():
    import psycopg2
    from config_store import get_db_url
    return psycopg2.connect(get_db_url())


# ── Hash / verificação de senha ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 200_000
    )
    return f"{salt}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hx = stored.split(":", 1)
        h = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), 200_000
        )
        return secrets.compare_digest(hx, h.hex())
    except Exception:
        return False


# ── Setup / inicialização ────────────────────────────────────────────────────

def setup_tables():
    """Cria tabelas de auth. Idempotente."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(_CREATE_SQL)
        c.commit()


def needs_setup() -> bool:
    """True se ainda não existir nenhum usuário (setup inicial pendente)."""
    try:
        with _conn() as c:
            with c.cursor() as cur:
                try:
                    cur.execute("SELECT COUNT(*) FROM auth_users")
                    return cur.fetchone()[0] == 0
                except Exception:
                    c.rollback()
                    cur.execute(_CREATE_SQL)
                    c.commit()
                    return True
    except Exception:
        return True


def create_admin(email: str, name: str, password: str) -> bool:
    """Cria o primeiro usuário admin. Só funciona se não existir nenhum."""
    if not needs_setup():
        return False
    return add_user(email, name, password, role="admin")


# ── CRUD de usuários ─────────────────────────────────────────────────────────

def add_user(email: str, name: str, password: str, role: str = "user") -> bool:
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO auth_users (email,name,role,password_hash)"
                    " VALUES (%s,%s,%s,%s)",
                    (email.lower().strip(), name.strip(), role,
                     hash_password(password)),
                )
            c.commit()
        return True
    except Exception:
        return False


def list_users() -> list:
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT id, email, name, role, is_active, created_at, last_login
                    FROM auth_users ORDER BY created_at
                """)
                cols = ["id","email","name","role","is_active","created_at","last_login"]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


def get_user_by_id(user_id: int) -> Optional[dict]:
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT id,email,name,role,is_active FROM auth_users WHERE id=%s",
                    (user_id,)
                )
                r = cur.fetchone()
        if not r:
            return None
        return dict(zip(["id","email","name","role","is_active"], r))
    except Exception:
        return None


def toggle_active(user_id: int) -> bool:
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE auth_users SET is_active = NOT is_active WHERE id=%s",
                    (user_id,)
                )
            c.commit()
        return True
    except Exception:
        return False


def remove_user(user_id: int) -> bool:
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM auth_users WHERE id=%s", (user_id,))
            c.commit()
        return True
    except Exception:
        return False


def change_password(user_id: int, new_password: str) -> bool:
    """Troca senha sem verificar a atual (uso admin)."""
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE auth_users SET password_hash=%s WHERE id=%s",
                    (hash_password(new_password), user_id)
                )
            c.commit()
        return True
    except Exception:
        return False


def change_own_password(user_id: int, current_password: str, new_password: str) -> tuple[bool, str]:
    """Troca a própria senha verificando a senha atual. Retorna (ok, mensagem_erro)."""
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT password_hash FROM auth_users WHERE id=%s AND is_active=TRUE",
                    (user_id,)
                )
                row = cur.fetchone()
        if not row:
            return False, "Usuário não encontrado."
        if not verify_password(current_password, row[0]):
            return False, "Senha atual incorreta."
        change_password(user_id, new_password)
        return True, ""
    except Exception:
        return False, "Erro ao atualizar senha. Tente novamente."


# ── Sessões ──────────────────────────────────────────────────────────────────

def login(email: str, password: str) -> Optional[str]:
    """Valida credenciais → retorna session token ou None."""
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT id,password_hash,is_active FROM auth_users WHERE email=%s",
                    (email.lower().strip(),)
                )
                row = cur.fetchone()
        if not row:
            return None
        uid, stored, active = row
        if not active or not verify_password(password, stored):
            return None
        token = secrets.token_hex(32)
        expires = datetime.utcnow() + timedelta(days=SESSION_DAYS)
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO auth_sessions (token,user_id,expires_at)"
                    " VALUES (%s,%s,%s)",
                    (token, uid, expires)
                )
                cur.execute(
                    "UPDATE auth_users SET last_login=NOW() WHERE id=%s",
                    (uid,)
                )
            c.commit()
        return token
    except Exception:
        return None


def get_user_from_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT u.id, u.email, u.name, u.role
                    FROM auth_sessions s
                    JOIN auth_users u ON u.id = s.user_id
                    WHERE s.token=%s
                      AND s.expires_at > NOW()
                      AND u.is_active = TRUE
                """, (token,))
                r = cur.fetchone()
        if not r:
            return None
        return dict(zip(["id","email","name","role"], r))
    except Exception:
        return None


def logout(token: str):
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE token=%s", (token,))
            c.commit()
    except Exception:
        pass


def purge_expired_sessions():
    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE expires_at < NOW()")
            c.commit()
    except Exception:
        pass
