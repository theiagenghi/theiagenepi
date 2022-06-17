import logging
from typing import MutableSequence, Optional

import sentry_sdk
import sqlalchemy as sa
from auth0.v3.exceptions import TokenValidationError
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.query import Query
from starlette.requests import Request

import aspen.api.error.http_exceptions as ex
from aspen.api.deps import get_db, get_settings
from aspen.api.settings import Settings
from aspen.auth.auth0_management import Auth0Client
from aspen.auth.device_auth import validate_auth_header
from aspen.database.models import Group, User, UserRole


def get_usergroup_query(session: AsyncSession, auth0_user_id: str) -> Query:
    return (
        sa.select(User)  # type: ignore
        .options(
            joinedload(User.user_roles).joinedload(UserRole.role),
        )  # type: ignore
        .filter(User.auth0_user_id == auth0_user_id)  # type: ignore
    )


async def setup_userinfo(session: AsyncSession, auth0_user_id: str) -> Optional[User]:
    sentry_sdk.set_user(
        {
            "requested_user_id": auth0_user_id,
        }
    )
    try:
        userquery = get_usergroup_query(session, auth0_user_id)
        userwait = await session.execute(userquery)
        user = userwait.unique().scalars().one()
        for urole in user.user_roles:
            print(urole.group_id)
            print(urole.role.name)
    except NoResultFound:
        sentry_sdk.capture_message(
            f"Requested auth0_user_id {auth0_user_id} not found in usergroup query."
        )
        return None
    sentry_sdk.set_user(
        {
            "id": user.id,
            "auth0_uid": user.auth0_user_id,
        }
    )
    return user


async def get_auth_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    auth_header = request.headers.get("authorization")
    auth0_user_id = None
    if auth_header:
        try:
            payload = validate_auth_header(
                auth_header, settings.AUTH0_DOMAIN, settings.AUTH0_CLIENT_ID
            )
            auth0_user_id = payload["sub"]
        except TokenValidationError as err:
            logging.warn(f"Token validation error: {err}")
    elif "profile" in request.session:
        auth0_user_id = request.session["profile"].get("user_id")
    # Redirect to Login page
    if not auth0_user_id:
        # TODO - redirect to login.
        raise ex.UnauthenticatedException("Login failure")
    found_auth_user = await setup_userinfo(session, auth0_user_id)
    if not found_auth_user:
        # login attempt from user not in DB
        # TODO - redirect to login.
        raise ex.UnauthenticatedException("Login failure")
    # There's a starlette-context module that allows us to manage
    # per-request data without depending on having a request object
    # available. For now we seem to have `request` when we need it,
    # but we can change this if necessary.
    request.state.auth_user = found_auth_user
    return found_auth_user


async def get_admin_user(auth_user: User = Depends(get_auth_user)) -> None:
    if not auth_user.system_admin:
        raise ex.UnauthorizedException("Not authorized")


async def get_auth0_apiclient(
    request: Request, settings: Settings = Depends(get_settings)
):
    client_id: str = settings.AUTH0_MANAGEMENT_CLIENT_ID
    client_secret: str = settings.AUTH0_MANAGEMENT_CLIENT_SECRET
    domain: str = settings.AUTH0_MANAGEMENT_DOMAIN
    auth0_client = Auth0Client(client_id, client_secret, domain)
    return auth0_client


class AuthContext:
    def __init__(
        self,
        user: User,
        group: Group,
        user_roles: MutableSequence[UserRole],
    ):
        self.user = user
        self.group = group
        self.user_roles = user_roles


async def require_group_membership(
    org_id: Optional[int],
    request: Request,
    user: User = Depends(get_auth_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MutableSequence[UserRole]:
    if org_id is None:
        return
    # Figure out whether this user is a *direct member* of the group
    # we're trying to get context for.
    query = (
        sa.select(UserRole)  # type: ignore
        .options(
            joinedload(UserRole.role, innerjoin=True),  # type: ignore
            joinedload(UserRole.group, innerjoin=True),  # type: ignore
        )
        .filter(UserRole.user == user)  # type: ignore
        .filter(UserRole.group_id == org_id)  # type: ignore
    )
    rolewait = await session.execute(query)
    user_roles = rolewait.unique().scalars().all()
    return user_roles


async def get_auth_context(
    org_id: Optional[int],  # NOTE - This comes from our route!
    user: User = Depends(get_auth_user),
    user_roles: MutableSequence[UserRole] = Depends(require_group_membership),
) -> AuthContext:
    group = None
    roles = []
    for row in user_roles:
        roles.append(row.role.name)
        group = row.group
    ac = AuthContext(user, group, roles)
    return ac
