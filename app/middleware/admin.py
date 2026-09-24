"""X-Admin-Key authentication for operator-only routes (`/api/v1/tenants/*`).

These routes act on any tenant by id, so they must never be reachable with a tenant's
API key. They are disabled (403) unless ADMIN_API_KEY is configured.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError

ADMIN_KEY_HEADER = "X-Admin-Key"

admin_key_scheme = APIKeyHeader(
    name=ADMIN_KEY_HEADER,
    scheme_name="AdminKeyAuth",
    description="Operator key (ADMIN_API_KEY). Never give it to tenants.",
    auto_error=False,
)


async def require_admin(
    provided: Annotated[str | None, Security(admin_key_scheme)],
) -> None:
    expected = settings.ADMIN_API_KEY
    if expected is None:
        raise ForbiddenError("The admin API is disabled. Set ADMIN_API_KEY to enable it.")
    if not provided:
        raise UnauthorizedError(
            f"Missing {ADMIN_KEY_HEADER} header", headers={"WWW-Authenticate": ADMIN_KEY_HEADER}
        )
    if not hmac.compare_digest(provided.encode(), expected.get_secret_value().encode()):
        raise UnauthorizedError("Invalid admin key", headers={"WWW-Authenticate": ADMIN_KEY_HEADER})


AdminDep = Annotated[None, Depends(require_admin)]
