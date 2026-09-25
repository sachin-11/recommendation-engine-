"""Outgoing email.

EMAIL_BACKEND picks the transport:
- ``smtp``: send through any SMTP server (Resend, SES, SendGrid, Gmail, or Mailpit locally).
- ``ses``: Amazon SES over SMTP, with the SMTP password derived from AWS_SECRET_ACCESS_KEY.
- ``console``: log the message, including its links, for local development without SMTP.
- ``memory``: keep messages in ``MemoryEmailSender.outbox`` for tests.

Sending never raises into a request: failures are logged, and the user can ask for the
email again.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "Email (EMAIL_BACKEND=console, not sent) to=%s subject=%r\n%s",
            message.to,
            message.subject,
            message.text,
        )


class MemoryEmailSender:
    outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


class SmtpEmailSender:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        security: str | None = None,
    ) -> None:
        resolved_host = host or settings.SMTP_HOST
        if not resolved_host:
            raise RuntimeError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        self._host: str = resolved_host
        self._port = port or settings.SMTP_PORT
        self._security = security or settings.SMTP_SECURITY
        self._timeout = settings.SMTP_TIMEOUT_SECONDS
        self._username = username or settings.SMTP_USERNAME
        self._password = password or (
            settings.SMTP_PASSWORD.get_secret_value() if settings.SMTP_PASSWORD else None
        )

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, self._build(message))

    def _build(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = settings.EMAIL_FROM
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = make_msgid(domain=settings.EMAIL_FROM.rsplit("@", 1)[-1].strip(">"))
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")
        return mime

    def _send(self, mime: MimeMessage) -> None:
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if self._security == "ssl":
            smtp = smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context)
        else:
            smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with smtp:
            if self._security == "starttls":
                smtp.starttls(context=context)
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(mime)


def ses_smtp_password(secret_access_key: str, region: str) -> str:
    """The SES SMTP password for an IAM secret key (AWS's documented SigV4 derivation)."""

    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    signature = sign(f"AWS4{secret_access_key}".encode(), "11111111")
    for part in (region, "ses", "aws4_request", "SendRawEmail"):
        signature = sign(signature, part)
    return base64.b64encode(bytes([0x04]) + signature).decode()


def ses_sender() -> SmtpEmailSender:
    if not (settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY):
        raise RuntimeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required for SES")
    return SmtpEmailSender(
        host=f"email-smtp.{settings.AWS_REGION}.amazonaws.com",
        port=587,
        username=settings.AWS_ACCESS_KEY_ID,
        password=ses_smtp_password(
            settings.AWS_SECRET_ACCESS_KEY.get_secret_value(), settings.AWS_REGION
        ),
        security="starttls",
    )


def get_email_sender() -> EmailSender:
    if settings.EMAIL_BACKEND == "smtp":
        return SmtpEmailSender()
    if settings.EMAIL_BACKEND == "ses":
        return ses_sender()
    if settings.EMAIL_BACKEND == "memory":
        return MemoryEmailSender()
    return ConsoleEmailSender()


def mask_address(address: str) -> str:
    """`sachin@example.com` -> `s***@example.com`, to keep addresses out of logs."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


async def send_email(message: EmailMessage) -> None:
    """Send, logging instead of raising; meant for background tasks."""
    try:
        await get_email_sender().send(message)
    except Exception:
        logger.exception("Could not send email %r to %s", message.subject, mask_address(message.to))
    else:
        logger.info("Email sent: %r to %s", message.subject, mask_address(message.to))
