"""
Email service — verification and password reset emails.

Uses Brevo (https://brevo.com) HTTP API — free tier: 300 emails/day.
Works on Render and any cloud host (no SMTP ports needed).
If BREVO_API_KEY is not set, the email link is logged to console
so local development works without any API key.
"""
import logging
import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _send(to: str, subject: str, html: str) -> None:
    if not settings.brevo_api_key:
        log.warning("BREVO_API_KEY not configured — printing email to console instead")
        log.info("TO: %s | SUBJECT: %s\n%s", to, subject, html)
        return

    payload = {
        "sender": {"name": settings.smtp_from_name, "email": settings.smtp_from_email},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    try:
        resp = httpx.post(
            BREVO_API_URL,
            json=payload,
            headers={"api-key": settings.brevo_api_key},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to send email to %s — %s", to, exc, exc_info=True)
        raise


def send_verification_email(to: str, token: str) -> None:
    link = f"{settings.backend_url}/auth/verify-email?token={token}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#f8f9fa;padding:40px 0">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;padding:40px">
    <h1 style="margin:0 0 4px;font-size:22px;color:#111">Welcome to CertMind</h1>
    <p style="color:#6b7280;margin:0 0 28px;font-size:14px">Verify your email to get started</p>
    <p style="color:#374151;font-size:14px;margin:0 0 24px">
      Click the button below to verify your email address. This link expires in <strong>24 hours</strong>.
    </p>
    <a href="{link}"
       style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;
              font-weight:600;font-size:14px;padding:12px 28px;border-radius:8px">
      Verify Email →
    </a>
    <p style="color:#9ca3af;font-size:12px;margin:28px 0 0">
      If you didn't create a CertMind account, you can safely ignore this email.
    </p>
  </div>
</body>
</html>
"""
    _send(to, "Verify your CertMind email", html)


def send_expiry_reminder_email(to: str, exam_slug: str, expires_at_iso: str) -> None:
    """Send a 'your subscription expires tomorrow' reminder email."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
        expires_str = dt.strftime("%B %d, %Y at %H:%M UTC")
    except Exception:
        expires_str = expires_at_iso

    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#f8f9fa;padding:40px 0">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;padding:40px">
    <h1 style="margin:0 0 4px;font-size:22px;color:#111">Your access expires soon</h1>
    <p style="color:#6b7280;margin:0 0 28px;font-size:14px">CertMind — {exam_slug.replace('-', ' ').title()}</p>
    <p style="color:#374151;font-size:14px;margin:0 0 24px">
      Your CertMind access for <strong>{exam_slug.replace('-', ' ').title()}</strong> expires on
      <strong>{expires_str}</strong>. After that you will need to purchase a new subscription.
    </p>
    <a href="{settings.frontend_url}/dashboard"
       style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;
              font-weight:600;font-size:14px;padding:12px 28px;border-radius:8px">
      Continue Studying →
    </a>
    <p style="color:#9ca3af;font-size:12px;margin:28px 0 0">
      Make the most of your remaining time — good luck on your exam!
    </p>
  </div>
</body>
</html>
"""
    _send(to, "Your CertMind access expires tomorrow", html)


def send_password_reset_email(to: str, token: str) -> None:
    link = f"{settings.frontend_url}/reset-password?token={token}"  # goes directly to frontend form
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#f8f9fa;padding:40px 0">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;padding:40px">
    <h1 style="margin:0 0 4px;font-size:22px;color:#111">Reset your password</h1>
    <p style="color:#6b7280;margin:0 0 28px;font-size:14px">CertMind account recovery</p>
    <p style="color:#374151;font-size:14px;margin:0 0 24px">
      Click the button below to set a new password. This link expires in <strong>1 hour</strong>.
    </p>
    <a href="{link}"
       style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;
              font-weight:600;font-size:14px;padding:12px 28px;border-radius:8px">
      Reset Password →
    </a>
    <p style="color:#9ca3af;font-size:12px;margin:28px 0 0">
      If you didn't request a password reset, you can safely ignore this email.
      Your password will not change.
    </p>
  </div>
</body>
</html>
"""
    _send(to, "Reset your CertMind password", html)
