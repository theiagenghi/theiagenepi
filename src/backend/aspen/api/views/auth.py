import os
from typing import List, Optional, Tuple
from urllib.parse import urlencode

import sqlalchemy as sa
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import StarletteOAuth2App
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import NoResultFound
from starlette.requests import Request
from starlette.responses import Response

import aspen.api.error.http_exceptions as ex
from aspen.api.authn import get_auth_user, get_cookie_userid, get_identity_provider
from aspen.api.deps import get_auth0_client, get_db, get_settings
from aspen.api.settings import APISettings
from aspen.auth.identity_provider import IdentityProvider, InvitationInfo
from aspen.database.models import Group, Role, User, UserRole

# From the example here:
# https://github.com/authlib/demo-oauth-client/tree/master/fastapi-google-login
router = APIRouter()


async def grant_invitation_roles(
    db: AsyncSession, user: User, org_id: str, role_names: List[str]
) -> Optional[Group]:
    """Give a user the roles an invitation promised them.

    Authorization lives entirely in our own database, so this is identical no
    matter which identity provider issued the invitation.
    """
    try:
        group = (
            (await db.execute(sa.select(Group).where(Group.auth0_org_id == org_id)))  # type: ignore
            .scalars()
            .one()
        )
    except NoResultFound:
        return None
    for role_name in role_names:
        role = (
            (await db.execute(sa.select(Role).where(Role.name == role_name)))  # type: ignore
            .scalars()
            .one_or_none()
        )
        if role is None:
            continue
        existing = (
            (
                await db.execute(
                    sa.select(UserRole)  # type: ignore
                    .where(UserRole.user_id == user.id)
                    .where(UserRole.group_id == group.id)
                    .where(UserRole.role_id == role.id)
                )
            )
            .scalars()
            .one_or_none()
        )
        if existing is None:
            db.add(UserRole(user_id=user.id, group_id=group.id, role_id=role.id))
    await db.commit()
    return group


async def get_invitation_redirect(
    oauth: StarletteOAuth2App,
    settings: APISettings,
    identity_provider: IdentityProvider,
    db: AsyncSession,
    request: Request,
    invitation: str,
    organization: str,
    organization_name: Optional[str] = None,
    cookie_userid: Optional[str] = None,
) -> Optional[Response]:
    # If this invitation is for an email address that already exists in our db,
    # we'll use our custom invitation acceptance flow to associate their existing
    # account with the invitation. If we haven't seen that email address before,
    # we'll send them to the standard auth0 "create a new account" flow.

    # Load more information about the invitation from the identity provider.
    invitation_info = await identity_provider.get_invitation(organization, invitation)
    if not invitation_info:
        return None
    # Check to see if the user is already in our db.
    invitee = invitation_info["invitee_email"]
    try:
        (await db.execute(sa.select(User).where(User.email == invitee))).scalars().one()  # type: ignore
    except NoResultFound:
        # The invitee has no account yet. Auth0 owns its own signup flow and can
        # redeem the invitation itself, so hand the invitation back to the
        # caller to forward. Any other provider only does authentication, so we
        # carry the invitation through login in the session instead.
        if settings.PROVISIONING_BACKEND == "auth0":
            return None
    # If we're already logged in as this user, just process the invitation and redirect to welcome.
    user = None
    try:
        user = (
            (
                await db.execute(
                    sa.select(User).where(User.auth0_user_id == cookie_userid)  # type: ignore
                )
            )
            .scalars()
            .one()
        )
    except NoResultFound:
        pass
    # If we're already logged in as the invited user, process the invitation!
    if user and invitee == user.email:
        # Redirect to process_invitation endpoint
        redirect_url = (
            settings.API_URL
            + f"/v2/auth/process_invitation?invitation={invitation}&organization={organization}&organization_name={organization_name}"
        )
        return RedirectResponse(redirect_url)
    # If we're not logged in, or logged in as a *different* user, we'll need to stash
    # the invitation in the session and redirect to the login page.
    request.session["process_invitation"] = {
        "invitation": invitation,
        "organization": organization,
        "organization_name": organization_name,
    }
    return await oauth.authorize_redirect(
        request, settings.AUTH0_CALLBACK_URL, login_hint=invitee, prompt="login"
    )


@router.get("/login")
async def login(
    request: Request,
    organization: Optional[str] = None,
    invitation: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    organization_name: Optional[str] = None,
    oauth: StarletteOAuth2App = Depends(get_auth0_client),
    identity_provider: IdentityProvider = Depends(get_identity_provider),
    settings: APISettings = Depends(get_settings),
    cookie_userid: Optional[str] = Depends(get_cookie_userid),
) -> Response:
    kwargs = {}
    if invitation and organization:
        resp = await get_invitation_redirect(
            oauth,
            settings,
            identity_provider,
            db,
            request,
            invitation,
            organization,
            organization_name,
            cookie_userid,
        )
        if resp:
            return resp
        kwargs["invitation"] = invitation
    if organization:
        kwargs["organization"] = organization
    if organization_name:
        kwargs["organization_name"] = organization_name
    return await oauth.authorize_redirect(
        request, settings.AUTH0_CALLBACK_URL, **kwargs
    )


async def create_user_if_not_exists(
    db, userinfo, pending_org_id: Optional[str] = None
) -> Tuple[User, Optional[Group]]:
    auth0_user_id = userinfo.get("sub")
    if not auth0_user_id:
        # User ID really needs to be present
        raise ex.UnauthorizedException("Invalid user id")
    userquery = await db.execute(
        sa.select(User).filter(User.auth0_user_id == auth0_user_id)  # type: ignore
    )
    try:
        # Return early if this user already exists
        user = userquery.scalars().one()
        return user, None
    except NoResultFound:
        pass
    # We're currently only creating new users if they're confirming an org
    # invitation. Auth0 signals that with an `org_id` claim; providers that only
    # do authentication have no such claim, so the caller passes the org id from
    # the invitation stashed in the session.
    org_id = userinfo.get("org_id") or pending_org_id
    if not org_id:
        raise ex.UnauthorizedException("Invalid group id")
    groupquery = await db.execute(
        sa.select(Group).filter(Group.auth0_org_id == org_id)  # type: ignore
    )
    # If the group doesn't exist, we can't create a user for it
    try:
        group = groupquery.scalars().one()  # type: ignore
    except NoResultFound:
        raise ex.UnauthorizedException("Unknown group")

    user_fields = {
        "name": userinfo["email"],
        "email": userinfo["email"],
        "auth0_user_id": auth0_user_id,
        "system_admin": False,
    }
    newuser = User(**user_fields)
    db.add(newuser)
    await db.commit()
    return newuser, group


@router.get("/callback")
async def auth(
    request: Request,
    oauth: StarletteOAuth2App = Depends(get_auth0_client),
    db: AsyncSession = Depends(get_db),
    error_description: Optional[str] = None,
    settings: APISettings = Depends(get_settings),
) -> Response:
    if error_description:
        # Note: Auth0 sends the message "invitation not found or already used" for *both* expired and
        # already-used tokens, so users will typically only see the already_accepted error. The "expired"
        # page becomes fallback in case there are any unknown errors auth0 sends.
        if "already used" in error_description:
            return RedirectResponse(
                os.getenv("FRONTEND_URL", "") + "/auth/invite/already_accepted"
            )
        else:
            return RedirectResponse(
                os.getenv("FRONTEND_URL", "") + "/auth/invite/expired"
            )
    try:
        token = await oauth.authorize_access_token(request)
    except OAuthError:
        raise ex.UnauthorizedException("Invalid token")
    userinfo = token.get("userinfo")
    if not userinfo:
        raise ex.UnauthorizedException("No user info in token")
    # Store the user information in flask session.
    request.session["jwt_payload"] = userinfo
    request.session["profile"] = {
        "user_id": userinfo["sub"],
        "name": userinfo["name"],
    }
    saved_invitation = request.session.get("process_invitation") or {}
    user, newuser_group = await create_user_if_not_exists(
        db, userinfo, saved_invitation.get("organization")
    )

    # If we saved an org invitation in the users's session, redirect the user to the endpoint
    # that can process the invitation, and clear out the invitation info in their session.
    if saved_invitation:
        invitation = saved_invitation.get("invitation")
        organization = saved_invitation.get("organization")
        organization_name = saved_invitation.get("organization_name")
        redirect_url = (
            settings.API_URL
            + f"/v2/auth/process_invitation?invitation={invitation}&organization={organization}&organization_name={organization_name}"
        )
        del request.session["process_invitation"]
        return RedirectResponse(redirect_url)
    # `newuser_group` is only set when we just created the account from an
    # organization, which is the case the welcome page exists for.
    if newuser_group:
        return RedirectResponse(
            os.getenv("FRONTEND_URL", "") + f"/welcome/{newuser_group.id}"
        )
    else:
        return RedirectResponse(os.getenv("FRONTEND_URL", "") + "/data/samples")


@router.get("/process_invitation")
async def process_invitation(
    request: Request,
    organization: str,
    invitation: str,
    db: AsyncSession = Depends(get_db),
    organization_name: Optional[str] = None,
    oauth: StarletteOAuth2App = Depends(get_auth0_client),
    identity_provider: IdentityProvider = Depends(get_identity_provider),
    settings: APISettings = Depends(get_settings),
    user=Depends(get_auth_user),
) -> Response:
    # Load more information about the invitation from the identity provider.
    invitation_info: Optional[InvitationInfo] = await identity_provider.get_invitation(
        organization, invitation
    )
    if not invitation_info:
        # Let the identity provider complain about the invalid invitation.
        kwargs = {
            "invitation": invitation,
            "organization": organization,
            "organization_name": organization_name,
        }
        return await oauth.authorize_redirect(
            request, settings.AUTH0_CALLBACK_URL, **kwargs
        )

    # Check to see if the user is the same as the one we're logged in as
    if invitation_info["invitee_email"] != user.email:
        raise ex.BadRequestException("email address mismatch")

    # If we made it to this point, just process the invitation and redirect to welcome.
    await identity_provider.accept_invitation(
        organization, invitation_info, user.auth0_user_id
    )
    group = await grant_invitation_roles(
        db, user, organization, invitation_info["roles"]
    )
    if not group:
        # This really shouldn't have happened, but send them to the frontend.
        return RedirectResponse(os.getenv("FRONTEND_URL", "") + "/data/samples")
    return RedirectResponse(os.getenv("FRONTEND_URL", "") + f"/welcome/{group.id}")


@router.get("/logout")
async def logout(
    request: Request, settings: APISettings = Depends(get_settings)
) -> Response:
    # Clear session stored data
    request.session.pop("jwt_payload", None)
    request.session.pop("profile", None)
    # Redirect user to logout endpoint
    params = {
        "returnTo": os.getenv("FRONTEND_URL"),
        "client_id": settings.AUTH0_CLIENT_ID,
    }
    return RedirectResponse(f"{settings.AUTH0_LOGOUT_URL}?{urlencode(params)}")
