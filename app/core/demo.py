"""The public demo: who is a demo visitor."""

from app.core.config import settings


def is_demo_email(email: str | None) -> bool:
    """Whether this is the demo workspace's visitor account, while the demo is on."""
    return bool(settings.DEMO_ENABLED and email and email == settings.DEMO_EMAIL.lower())
