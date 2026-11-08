"""Transactional email transport.

A technical concern only: this module knows how to put a message on the wire,
never why it is being sent. Subjects and bodies belong to the domain that sends
them - for password recovery, `services/auth/emails.py`.

Which backend runs is decided by configuration, not by a flag at the call site:

- `RESEND_API_KEY` set  -> Resend, over its HTTPS API.
- key absent            -> the console backend, which logs the message in full.

The console backend exists so the whole recovery flow can be walked locally
without an email account. It prints the reset link, which is exactly what the
recipient would have clicked - a live credential. So it only does that while
`FRONTEND_BASE_URL` points at this machine; anywhere else a missing key is a
failed delivery, reported without the message.
"""

import logging
from urllib.parse import urlparse

import resend
from resend.exceptions import ResendError

from services.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """The provider refused the message or could not be reached."""


def _send_via_resend(sender: str, recipient: str, subject: str, html: str, text: str) -> str:
    settings = get_settings()
    # The SDK reads its credential from module state; set it per call so a
    # rotated key is picked up without a restart.
    resend.api_key = settings.resend_api_key

    params: resend.Emails.SendParams = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": html,
        "text": text,
    }
    try:
        response = resend.Emails.send(params)
    except ResendError as error:
        raise EmailDeliveryError(f"Resend rejected the message: {error}") from error
    except Exception as error:  # network failure, DNS, TLS - never a 200
        raise EmailDeliveryError(f"Could not reach Resend: {error}") from error

    message_id = response.get("id") if isinstance(response, dict) else None
    if not message_id:
        # Accepted but unacknowledged: reported as a failed delivery rather than
        # escaping as a KeyError from the background task that sent it.
        raise EmailDeliveryError("Resend answered without a message id")
    return str(message_id)


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in _LOCAL_HOSTS


def _send_via_console(sender: str, recipient: str, subject: str, text: str) -> str:
    if not _is_local(get_settings().frontend_base_url):
        # A deployment that forgot its key must not write working reset links
        # (or the addresses they were meant for) into its logs.
        raise EmailDeliveryError(
            "RESEND_API_KEY is not set and FRONTEND_BASE_URL is not local, so the message "
            "was neither sent nor logged. Set RESEND_API_KEY."
        )

    logger.warning(
        "RESEND_API_KEY is not set, so no mail was sent. The message follows.\n"
        "From:    %s\nTo:      %s\nSubject: %s\n\n%s",
        sender,
        recipient,
        subject,
        text,
    )
    return "console"


def send_email(recipient: str, subject: str, html: str, text: str) -> str:
    """Send one message and return the provider's id for it.

    Raises `EmailDeliveryError` when the send failed, or when there is no
    provider key outside a local setup.
    """
    settings = get_settings()
    if not settings.email_is_live:
        return _send_via_console(settings.email_from, recipient, subject, text)
    return _send_via_resend(settings.email_from, recipient, subject, html, text)
