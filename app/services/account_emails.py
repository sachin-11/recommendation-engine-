"""Account emails: verify address, reset password, password changed.

Each has a plain-text part (what most security-conscious clients show) and a small inline
HTML part. Links point at the dashboard, which calls the API with the token.
"""

from html import escape
from urllib.parse import urlencode

from app.core.config import settings
from app.core.email import EmailMessage
from app.models.tenant import Tenant

PRODUCT = "RecoEngine"


def dashboard_link(path: str, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.DASHBOARD_URL}{path}{query}"


def _html(heading: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> str:
    body = "".join(
        f'<p style="margin:0 0 16px;line-height:1.55">{escape(p)}</p>' for p in paragraphs
    )
    if button:
        label, url = button
        body += (
            f'<p style="margin:24px 0"><a href="{escape(url)}" style="background:#2563eb;'
            "color:#ffffff;padding:12px 20px;border-radius:6px;text-decoration:none;"
            f'font-weight:600;display:inline-block">{escape(label)}</a></p>'
            '<p style="margin:0 0 16px;font-size:13px;color:#555">Or paste this link into '
            f'your browser:<br><span style="word-break:break-all">{escape(url)}</span></p>'
        )
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;padding:24px;color:#111">'
        f'<p style="font-weight:700;font-size:15px;margin:0 0 24px">{PRODUCT}</p>'
        f'<h1 style="font-size:20px;margin:0 0 16px">{escape(heading)}</h1>{body}'
        f'<p style="margin:32px 0 0;font-size:12px;color:#777">{escape(footer)}</p></div>'
    )


def verification_email(tenant: Tenant, token: str) -> EmailMessage:
    url = dashboard_link("/verify-email", token=token)
    hours = settings.EMAIL_VERIFICATION_TTL_HOURS
    lines = [
        f"Hi {tenant.name},",
        f"Confirm that {tenant.email} is your address to finish setting up {PRODUCT}. "
        "Once it is confirmed you can create API keys and upload your full catalogue.",
    ]
    footer = f"The link works once and expires in {hours} hours. Didn't sign up? Ignore this email."
    return EmailMessage(
        to=tenant.email,
        subject=f"Confirm your email for {PRODUCT}",
        text="\n\n".join([*lines, f"Confirm your email: {url}", footer]),
        html=_html("Confirm your email", lines, ("Confirm email", url), footer),
    )


def password_reset_email(tenant: Tenant, token: str) -> EmailMessage:
    url = dashboard_link("/reset-password", token=token)
    minutes = settings.PASSWORD_RESET_TTL_MINUTES
    lines = [
        f"Hi {tenant.name},",
        f"Someone asked to reset the {PRODUCT} password for {tenant.email}. "
        "Choose a new password with the link below. Your API keys keep working; "
        "dashboard sessions are signed out.",
    ]
    footer = (
        f"The link works once and expires in {minutes} minutes. "
        "Didn't ask for this? Ignore this email; your password stays the same."
    )
    return EmailMessage(
        to=tenant.email,
        subject=f"Reset your {PRODUCT} password",
        text="\n\n".join([*lines, f"Reset your password: {url}", footer]),
        html=_html("Reset your password", lines, ("Choose a new password", url), footer),
    )


def password_changed_email(tenant: Tenant) -> EmailMessage:
    url = dashboard_link("/forgot-password")
    lines = [
        f"Hi {tenant.name},",
        f"The {PRODUCT} password for {tenant.email} was just changed and every dashboard "
        "session was signed out.",
        "If this wasn't you, reset your password now and revoke any API keys you don't recognise.",
    ]
    footer = "You're receiving this because it is a security change on your account."
    return EmailMessage(
        to=tenant.email,
        subject=f"Your {PRODUCT} password was changed",
        text="\n\n".join([*lines, f"Reset your password: {url}", footer]),
        html=_html("Your password was changed", lines, ("Reset password", url), footer),
    )
