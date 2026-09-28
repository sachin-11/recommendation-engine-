"""Grant, revoke or list platform admins: the people who can open the /admin area and act
on every workspace (list, suspend, set limits).

    python scripts/platform_admin.py list
    python scripts/platform_admin.py grant owner@yourcompany.com
    python scripts/platform_admin.py revoke owner@yourcompany.com

There is deliberately no API for this: whoever can run it already controls the server.
The user must exist (sign up in the dashboard first). Their open sessions pick up the change
on the next request.

In production:
    docker compose -f docker-compose.prod.yml exec app python scripts/platform_admin.py list
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402


async def list_admins() -> int:
    async with AsyncSessionLocal() as session:
        admins = (
            await session.scalars(select(User).where(User.is_platform_admin).order_by(User.email))
        ).all()
    if not admins:
        print("No platform admins. Grant one with: grant <email>")
    for user in admins:
        print(f"{user.email}  ({user.name}, workspace {user.tenant_id})")
    return 0


async def set_admin(email: str, value: bool) -> int:
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is None:
            print(f"No user with email {email}. Sign up in the dashboard first.", file=sys.stderr)
            return 1
        if not user.is_active:
            print(f"{user.email} is deactivated.", file=sys.stderr)
            return 1
        user.is_platform_admin = value
        await session.commit()
    print(f"{user.email} is {'now' if value else 'no longer'} a platform admin.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show every platform admin")
    for name in ("grant", "revoke"):
        sub.add_parser(name, help=f"{name} platform admin rights").add_argument("email")
    args = parser.parse_args()
    try:
        if args.command == "list":
            return await list_admins()
        return await set_admin(args.email, args.command == "grant")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
