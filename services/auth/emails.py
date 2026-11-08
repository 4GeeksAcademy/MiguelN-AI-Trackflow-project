"""The password reset message.

Subject and body live here rather than in `services/core/email.py`, which is
transport only. Nothing in this module knows which provider carries the mail.

The HTML is deliberately old-fashioned - tables, inline styles, no external CSS
- because that is what renders the same in Gmail, Outlook and Apple Mail. It is
one fluid column capped at 600px, so on a phone it fills the screen instead of
being zoomed out to fit; body text is 16px so it is legible without pinching,
and the button is a 48px-tall block, comfortably above the 44px minimum for a
thumb. Every message also ships a plain-text part, which is what a text-only
client shows and what the console backend logs.
"""

import logging
from urllib.parse import quote

from services.core.config import get_settings
from services.core.email import EmailDeliveryError, send_email
from services.core.security import password_reset_expires_in_minutes

logger = logging.getLogger(__name__)

SUBJECT = "Reset your TrackFlow password"

BRAND_BLUE = "#2563EB"
INK = "#0F172A"
MUTED = "#475569"
HAIRLINE = "#E2E8F0"
CANVAS = "#F1F5F9"


def build_reset_url(token: str) -> str:
    """The link in the email: the frontend route that collects the new password."""
    settings = get_settings()
    return f"{settings.frontend_base_url}/reset-password?token={quote(token, safe='')}"


def _expiry_phrase(minutes: int) -> str:
    return "1 hour" if minutes == 60 else f"{minutes} minutes"


def build_reset_email(reset_url: str, expires_in_minutes: int) -> tuple[str, str]:
    """Return the (html, text) parts of the reset message."""
    window = _expiry_phrase(expires_in_minutes)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SUBJECT}</title>
</head>
<body style="margin:0;padding:0;background-color:{CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{CANVAS};padding:24px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;background-color:#FFFFFF;border-radius:12px;
                    border:1px solid {HAIRLINE};font-family:-apple-system,BlinkMacSystemFont,
                    'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr>
          <td style="padding:28px 28px 8px 28px;">
            <span style="font-size:20px;font-weight:700;color:{BRAND_BLUE};
                         letter-spacing:-0.3px;">TrackFlow</span>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 28px 0 28px;">
            <h1 style="margin:0 0 16px 0;font-size:24px;line-height:1.3;color:{INK};
                       font-weight:700;">Reset your password</h1>
            <p style="margin:0 0 16px 0;font-size:16px;line-height:1.6;color:{MUTED};">
              We received a request to reset the password for this TrackFlow account.
              Tap the button below to choose a new one.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 28px 4px 28px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td align="center" bgcolor="{BRAND_BLUE}" style="border-radius:8px;">
                  <a href="{reset_url}"
                     style="display:block;padding:14px 24px;font-size:16px;font-weight:600;
                            line-height:20px;color:#FFFFFF;text-decoration:none;
                            border-radius:8px;">Reset my password</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 28px 0 28px;">
            <p style="margin:0 0 8px 0;font-size:14px;line-height:1.6;color:{MUTED};">
              This link expires in {window} and can only be used once.
            </p>
            <p style="margin:0 0 8px 0;font-size:14px;line-height:1.6;color:{MUTED};">
              If the button does not work, copy this address into your browser:
            </p>
            <p style="margin:0;font-size:13px;line-height:1.6;word-break:break-all;">
              <a href="{reset_url}" style="color:{BRAND_BLUE};">{reset_url}</a>
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px 28px 28px;">
            <hr style="border:none;border-top:1px solid {HAIRLINE};margin:0 0 16px 0;">
            <p style="margin:0;font-size:13px;line-height:1.6;color:{MUTED};">
              Did not request this? You can ignore this email - your password stays
              as it is, and the link above will expire on its own.
            </p>
          </td>
        </tr>
      </table>
      <p style="max-width:600px;margin:16px auto 0 auto;font-size:12px;line-height:1.5;
                color:{MUTED};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
                Roboto,Helvetica,Arial,sans-serif;">
        TrackFlow &middot; Faster routes, smarter deliveries
      </p>
    </td>
  </tr>
</table>
</body>
</html>"""

    text = f"""Reset your TrackFlow password

We received a request to reset the password for this TrackFlow account.
Open the link below to choose a new one:

{reset_url}

This link expires in {window} and can only be used once.

Did not request this? You can ignore this email - your password stays as it
is, and the link above will expire on its own.

TrackFlow - Faster routes, smarter deliveries
"""
    return html, text


def send_password_reset_email(recipient: str, token: str) -> bool:
    """Mail the reset link. Returns whether it went out.

    Never raises. This runs after the response has been sent, and the route it
    serves answers identically whether or not the address exists, so a delivery
    problem is something to find in the log, not something the caller is told.
    """
    reset_url = build_reset_url(token)
    html, text = build_reset_email(reset_url, password_reset_expires_in_minutes())
    try:
        message_id = send_email(recipient=recipient, subject=SUBJECT, html=html, text=text)
    except EmailDeliveryError:
        logger.exception("Could not deliver the password reset email")
        return False

    logger.info("Password reset email accepted by the provider (id %s)", message_id)
    return True
