"""Platform admin: list and inspect every workspace, suspend and reactivate, set limits,
see each workspace's billing and give complimentary Pro."""

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import Depends
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.api_key import ApiKey
from app.models.base import utcnow
from app.models.item import Item
from app.models.recommendation_log import RecommendationLog
from app.models.tenant import Plan, Tenant
from app.models.token_usage import TokenUsage
from app.models.user import Role, User
from app.schemas.account import UserResponse
from app.schemas.admin import (
    ComplimentaryGrant,
    DailyCount,
    LimitsUpdate,
    PlatformOverview,
    TopWorkspace,
    WorkspaceBillingOut,
    WorkspaceDetail,
    WorkspaceLimitsOut,
    WorkspaceList,
    WorkspaceSort,
    WorkspaceStatus,
    WorkspaceSummary,
)
from app.schemas.tenant import ApiKeyResponse
from app.services.analytics_service import AnalyticsService
from app.services.billing.plans import PAID_STATUSES, entitled_plan, is_complimentary
from app.services.workspace_limits import month_start

log = structlog.get_logger(__name__)

TOP_WORKSPACES = 5
DAILY_DAYS = 30


def _cost(tokens: int) -> float:
    return round(tokens * settings.EMBEDDING_PRICE_PER_MILLION_TOKENS / 1_000_000, 6)


def _stripe_customer_url(customer_id: str | None) -> str | None:
    if not customer_id:
        return None
    key = settings.STRIPE_SECRET_KEY.get_secret_value() if settings.STRIPE_SECRET_KEY else ""
    mode = "test/" if key.startswith(("sk_test_", "rk_test_")) else ""
    return f"https://dashboard.stripe.com/{mode}customers/{customer_id}"


def _billing(tenant: Tenant) -> WorkspaceBillingOut:
    return WorkspaceBillingOut(
        plan=entitled_plan(tenant),
        subscribed_plan=tenant.plan,
        subscription_status=tenant.subscription_status,
        current_period_end=tenant.current_period_end,
        cancel_at_period_end=tenant.cancel_at_period_end,
        stripe_customer_url=_stripe_customer_url(tenant.stripe_customer_id),
        complimentary=is_complimentary(tenant),
        complimentary_since=tenant.comp_pro_since,
        complimentary_until=tenant.comp_pro_until,
        complimentary_reason=tenant.comp_pro_reason,
    )


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Workspaces ---

    def _summary_query(self) -> Select[Any]:
        """Tenants with their counts, one row each. Aggregates are joined as subqueries so
        the list can be sorted and paged in the database."""
        month = month_start()
        items = (
            select(Item.tenant_id, func.count().label("n"))
            .group_by(Item.tenant_id)
            .subquery("items")
        )
        members = (
            select(User.tenant_id, func.count().label("n"))
            .group_by(User.tenant_id)
            .subquery("members")
        )
        owners = select(User.tenant_id, User.name).where(User.role == Role.OWNER).subquery("owners")
        queries = (
            select(RecommendationLog.tenant_id, func.count().label("n"))
            .where(RecommendationLog.created_at >= month)
            .group_by(RecommendationLog.tenant_id)
            .subquery("queries")
        )
        tokens = (
            select(TokenUsage.tenant_id, func.sum(TokenUsage.tokens).label("n"))
            .where(TokenUsage.day >= month.date())
            .group_by(TokenUsage.tenant_id)
            .subquery("tokens")
        )
        activity = (
            select(ApiKey.tenant_id, func.max(ApiKey.last_used_at).label("at"))
            .group_by(ApiKey.tenant_id)
            .subquery("activity")
        )
        return (
            select(
                Tenant,
                owners.c.name.label("owner_name"),
                func.coalesce(members.c.n, 0).label("members"),
                func.coalesce(items.c.n, 0).label("items"),
                func.coalesce(queries.c.n, 0).label("queries"),
                func.coalesce(tokens.c.n, 0).label("tokens"),
                activity.c.at.label("last_active_at"),
            )
            .outerjoin(owners, owners.c.tenant_id == Tenant.id)
            .outerjoin(members, members.c.tenant_id == Tenant.id)
            .outerjoin(items, items.c.tenant_id == Tenant.id)
            .outerjoin(queries, queries.c.tenant_id == Tenant.id)
            .outerjoin(tokens, tokens.c.tenant_id == Tenant.id)
            .outerjoin(activity, activity.c.tenant_id == Tenant.id)
        )

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        tenant: Tenant = row.Tenant
        tokens = int(row.tokens)
        return {
            "id": tenant.id,
            "name": tenant.name,
            "email": tenant.email,
            "domain_type": tenant.domain_type,
            "status": WorkspaceStatus.ACTIVE if tenant.is_active else WorkspaceStatus.SUSPENDED,
            "created_at": tenant.created_at,
            "owner_name": row.owner_name,
            "members": int(row.members),
            "items": int(row.items),
            "queries_this_month": int(row.queries),
            "tokens_this_month": tokens,
            "estimated_cost_this_month_usd": _cost(tokens),
            "last_active_at": row.last_active_at,
            "limits": WorkspaceLimitsOut(
                max_items=tenant.max_items,
                monthly_query_limit=tenant.monthly_query_limit,
                rate_limit_rpm=tenant.rate_limit_rpm,
            ),
            "billing": _billing(tenant),
        }

    async def list_workspaces(
        self,
        *,
        search: str | None,
        status: WorkspaceStatus | None,
        sort: WorkspaceSort,
        page: int,
        page_size: int,
    ) -> WorkspaceList:
        query = self._summary_query()
        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                or_(func.lower(Tenant.name).like(pattern), func.lower(Tenant.email).like(pattern))
            )
        if status is not None:
            query = query.where(Tenant.is_active.is_(status is WorkspaceStatus.ACTIVE))

        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        orders: dict[WorkspaceSort, list[ColumnElement[Any]]] = {
            WorkspaceSort.NEWEST: [Tenant.created_at.desc()],
            WorkspaceSort.NAME: [func.lower(Tenant.name)],
            WorkspaceSort.QUERIES: [query.selected_columns["queries"].desc()],
            WorkspaceSort.TOKENS: [query.selected_columns["tokens"].desc()],
            WorkspaceSort.ITEMS: [query.selected_columns["items"].desc()],
            # Never-used workspaces last.
            WorkspaceSort.LAST_ACTIVE: [
                query.selected_columns["last_active_at"].is_(None),
                query.selected_columns["last_active_at"].desc(),
            ],
        }
        rows = await self._session.execute(
            query.order_by(*orders[sort], Tenant.id).offset((page - 1) * page_size).limit(page_size)
        )
        return WorkspaceList(
            workspaces=[WorkspaceSummary(**self._summary(row)) for row in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
            pages=math.ceil((total or 0) / page_size),
        )

    async def get_workspace(self, tenant_id: uuid.UUID) -> WorkspaceDetail:
        row = (
            await self._session.execute(self._summary_query().where(Tenant.id == tenant_id))
        ).one_or_none()
        if row is None:
            raise NotFoundError(f"Workspace {tenant_id} not found")
        tenant: Tenant = row.Tenant
        analytics = AnalyticsService(self._session, tenant)
        usage = await analytics.usage(DAILY_DAYS)
        tokens = await analytics.tokens(DAILY_DAYS)
        members = await self._session.scalars(
            select(User).where(User.tenant_id == tenant.id).order_by(User.created_at, User.id)
        )
        keys = await self._session.scalars(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant.id, ApiKey.is_session.is_(False))
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
        )
        return WorkspaceDetail(
            **self._summary(row),
            suspended_at=tenant.suspended_at,
            suspended_reason=tenant.suspended_reason,
            email_verified=tenant.email_verified,
            embedding_status=await analytics.embedding_status(),
            queries_daily=[DailyCount(date=d["date"], count=d["count"]) for d in usage["daily"]],
            tokens_last_30_days=tokens["total_tokens"],
            member_list=[UserResponse.model_validate(u) for u in members],
            api_keys=[ApiKeyResponse.model_validate(k) for k in keys],
        )

    async def suspend(self, tenant_id: uuid.UUID, reason: str, admin: User) -> WorkspaceDetail:
        tenant = await self._tenant(tenant_id)
        if tenant.id == admin.tenant_id:
            raise BadRequestError("You cannot suspend your own workspace.")
        tenant.is_active = False
        tenant.suspended_at = utcnow()
        tenant.suspended_reason = reason
        await self._session.commit()
        log.info("workspace_suspended", tenant_id=str(tenant.id), by=admin.email, reason=reason)
        return await self.get_workspace(tenant.id)

    async def activate(self, tenant_id: uuid.UUID, admin: User) -> WorkspaceDetail:
        tenant = await self._tenant(tenant_id)
        tenant.is_active = True
        tenant.suspended_at = None
        tenant.suspended_reason = None
        await self._session.commit()
        log.info("workspace_activated", tenant_id=str(tenant.id), by=admin.email)
        return await self.get_workspace(tenant.id)

    async def update_limits(
        self, tenant_id: uuid.UUID, update: LimitsUpdate, admin: User
    ) -> WorkspaceDetail:
        tenant = await self._tenant(tenant_id)
        changes = update.model_dump(include=update.model_fields_set)
        for field, value in changes.items():
            setattr(tenant, field, value)
        await self._session.commit()
        log.info("workspace_limits_changed", tenant_id=str(tenant.id), by=admin.email, **changes)
        return await self.get_workspace(tenant.id)

    async def grant_complimentary(
        self, tenant_id: uuid.UUID, grant: ComplimentaryGrant, admin: User
    ) -> WorkspaceDetail:
        """Pro without a subscription, from now, for `days` or until revoked. A new grant
        replaces the previous one."""
        tenant = await self._tenant(tenant_id)
        now = utcnow()
        tenant.comp_pro_since = now
        tenant.comp_pro_until = now + timedelta(days=grant.days) if grant.days else None
        tenant.comp_pro_reason = grant.reason
        await self._session.commit()
        log.info(
            "complimentary_pro_granted",
            tenant_id=str(tenant.id),
            by=admin.email,
            days=grant.days,
            reason=grant.reason,
        )
        return await self.get_workspace(tenant.id)

    async def revoke_complimentary(self, tenant_id: uuid.UUID, admin: User) -> WorkspaceDetail:
        tenant = await self._tenant(tenant_id)
        tenant.comp_pro_since = tenant.comp_pro_until = tenant.comp_pro_reason = None
        await self._session.commit()
        log.info("complimentary_pro_revoked", tenant_id=str(tenant.id), by=admin.email)
        return await self.get_workspace(tenant.id)

    async def _tenant(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError(f"Workspace {tenant_id} not found")
        return tenant

    # --- Platform ---

    async def overview(self) -> PlatformOverview:
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = month_start(now)
        logs = RecommendationLog

        active, suspended = 0, 0
        for is_active, n in await self._session.execute(
            select(Tenant.is_active, func.count()).group_by(Tenant.is_active)
        ):
            if is_active:
                active = n
            else:
                suspended = n

        first_day = (today - timedelta(days=DAILY_DAYS - 1)).date()
        since = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
        day = func.date(logs.created_at)
        per_day = {
            str(d): n
            for d, n in await self._session.execute(
                select(day, func.count()).where(logs.created_at >= since).group_by(day)
            )
        }
        daily = []
        for offset in range(DAILY_DAYS):
            date = (first_day + timedelta(days=offset)).isoformat()
            daily.append(DailyCount(date=date, count=per_day.get(date, 0)))

        tokens_month = await self._session.scalar(
            select(func.coalesce(func.sum(TokenUsage.tokens), 0)).where(
                TokenUsage.day >= month.date()
            )
        )
        summaries = self._summary_query()
        top = await self._session.execute(
            summaries.order_by(summaries.selected_columns["queries"].desc(), Tenant.id).limit(
                TOP_WORKSPACES
            )
        )
        return PlatformOverview(
            workspaces_total=active + suspended,
            workspaces_active=active,
            workspaces_suspended=suspended,
            workspaces_new_last_30_days=await self._count(
                select(func.count()).where(Tenant.created_at >= now - timedelta(days=30))
            ),
            users_total=await self._count(select(func.count()).select_from(User)),
            items_total=await self._count(select(func.count()).select_from(Item)),
            queries_today=await self._count(select(func.count()).where(logs.created_at >= today)),
            queries_this_month=await self._count(
                select(func.count()).where(logs.created_at >= month)
            ),
            tokens_this_month=int(tokens_month or 0),
            estimated_cost_this_month_usd=_cost(int(tokens_month or 0)),
            pro_paying=await self._count(
                select(func.count()).where(
                    Tenant.plan == Plan.PRO, Tenant.subscription_status.in_(PAID_STATUSES)
                )
            ),
            pro_complimentary=await self._count(
                select(func.count()).where(
                    Tenant.comp_pro_since.is_not(None),
                    or_(Tenant.comp_pro_until.is_(None), Tenant.comp_pro_until > now),
                )
            ),
            payments_past_due=await self._count(
                select(func.count()).where(Tenant.subscription_status == "past_due")
            ),
            queries_daily=daily,
            top_workspaces=[
                TopWorkspace(
                    id=row.Tenant.id,
                    name=row.Tenant.name,
                    queries_this_month=int(row.queries),
                    tokens_this_month=int(row.tokens),
                )
                for row in top
                if row.queries or row.tokens
            ],
        )

    async def _count(self, query: Select[Any]) -> int:
        return int(await self._session.scalar(query) or 0)


def get_admin_service(session: Annotated[AsyncSession, Depends(get_db)]) -> AdminService:
    return AdminService(session)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
