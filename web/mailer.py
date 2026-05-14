"""
mailer.py — Envio de e-mails via SMTP.
Usa apenas stdlib (smtplib + email), sem dependências externas.

Variáveis de ambiente necessárias:
  SMTP_HOST      ex: smtp.gmail.com
  SMTP_PORT      ex: 587  (TLS)  ou  465  (SSL)
  SMTP_USER      seu e-mail (remetente)
  SMTP_PASSWORD  senha ou App Password (Gmail)
  SMTP_FROM      opcional — nome + e-mail exibido; padrão = SMTP_USER
  APP_URL        URL pública da plataforma ex: https://crmtrafegopago.vercel.app
"""
import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _cfg():
    return {
        "host":     os.getenv("SMTP_HOST", ""),
        "port":     int(os.getenv("SMTP_PORT", "587")),
        "user":     os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from":     os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", ""),
        "app_url":  os.getenv("APP_URL", "https://crmtrafegopago.vercel.app"),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["user"] and c["password"])


def send_welcome(to_email: str, to_name: str, password: str) -> tuple[bool, str]:
    """
    Envia e-mail de boas-vindas com as credenciais de acesso.
    Retorna (sucesso: bool, mensagem_erro: str).
    """
    if not is_configured():
        return False, "E-mail não configurado (SMTP_HOST/SMTP_USER/SMTP_PASSWORD ausentes)."

    c = _cfg()
    app_url = c["app_url"]
    subject = "Seu acesso à plataforma IXCTraffic"
    html = _welcome_html(to_name, to_email, password, app_url)
    text = _welcome_text(to_name, to_email, password, app_url)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = c["from"]
    msg["To"]      = to_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html",  "utf-8"))

    try:
        if c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=10) as srv:
                srv.login(c["user"], c["password"])
                srv.sendmail(c["from"], [to_email], msg.as_bytes())
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=10) as srv:
                srv.ehlo()
                srv.starttls()
                srv.ehlo()
                srv.login(c["user"], c["password"])
                srv.sendmail(c["from"], [to_email], msg.as_bytes())
        logger.info("Welcome e-mail sent to %s", to_email)
        return True, ""
    except Exception as e:
        logger.error("Failed to send welcome e-mail to %s: %s", to_email, e)
        return False, str(e)


# ── Templates de e-mail ─────────────────────────────────────────────────────

def _welcome_html(name: str, email: str, password: str, app_url: str) -> str:
    first = name.split()[0] if name else "Olá"
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f0f;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f0f;padding:40px 16px;">
  <tr><td align="center">
    <table width="520" cellpadding="0" cellspacing="0" style="background:#1a1a1a;border-radius:12px;overflow:hidden;border:1px solid #2a2a2a;">

      <!-- Header -->
      <tr>
        <td style="background:#a3ff00;padding:20px 32px;text-align:center;">
          <span style="font-size:1.3rem;font-weight:900;color:#0a0a0a;letter-spacing:-0.5px;">IXCTraffic</span>
          <br>
          <span style="font-size:0.72rem;color:#1a1a1a;font-weight:600;letter-spacing:1px;">PLATAFORMA DE TRÁFEGO PAGO</span>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:32px;">
          <p style="margin:0 0 8px;font-size:1.1rem;font-weight:700;color:#fff;">Olá, {first}! 👋</p>
          <p style="margin:0 0 24px;font-size:0.9rem;color:#aaa;line-height:1.6;">
            Sua conta na plataforma IXCTraffic foi criada.<br>
            Use as credenciais abaixo para acessar.
          </p>

          <!-- Credenciais -->
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#111;border:1px solid #2a2a2a;border-radius:8px;margin-bottom:24px;">
            <tr>
              <td style="padding:16px 20px;border-bottom:1px solid #2a2a2a;">
                <span style="font-size:0.7rem;font-weight:700;color:#a3ff00;letter-spacing:1px;">LOGIN</span><br>
                <span style="font-size:0.95rem;color:#fff;font-family:monospace;">{email}</span>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 20px;">
                <span style="font-size:0.7rem;font-weight:700;color:#a3ff00;letter-spacing:1px;">SENHA INICIAL</span><br>
                <span style="font-size:0.95rem;color:#fff;font-family:monospace;">{password}</span>
              </td>
            </tr>
          </table>

          <!-- Aviso de segurança -->
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:rgba(163,255,0,0.08);border:1px solid rgba(163,255,0,0.2);
                        border-radius:8px;margin-bottom:28px;">
            <tr>
              <td style="padding:12px 16px;font-size:0.82rem;color:#c8ff66;line-height:1.5;">
                <strong>Recomendado:</strong> após o primeiro acesso, vá em
                <strong>Alterar senha</strong> (rodapé do menu lateral) e defina
                uma senha pessoal.
              </td>
            </tr>
          </table>

          <!-- Botão -->
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td align="center">
                <a href="{app_url}" target="_blank"
                   style="display:inline-block;background:#a3ff00;color:#0a0a0a;
                          font-weight:700;font-size:0.9rem;padding:12px 36px;
                          border-radius:8px;text-decoration:none;letter-spacing:0.3px;">
                  Acessar plataforma →
                </a>
              </td>
            </tr>
          </table>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="padding:16px 32px;border-top:1px solid #2a2a2a;text-align:center;">
          <p style="margin:0;font-size:0.75rem;color:#555;">
            Este e-mail foi enviado automaticamente pela plataforma IXCTraffic.<br>
            Não responda a este e-mail.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def _welcome_text(name: str, email: str, password: str, app_url: str) -> str:
    first = name.split()[0] if name else "Olá"
    return f"""Olá, {first}!

Sua conta na plataforma IXCTraffic foi criada.

LOGIN:  {email}
SENHA:  {password}

Acesse: {app_url}

Recomendado: após o primeiro acesso, altere sua senha em
Alterar senha (rodapé do menu lateral).

---
E-mail automático da plataforma IXCTraffic. Não responda.
"""
