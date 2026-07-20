import pytest
import sqlalchemy as sa
from authlib.integrations.starlette_client import StarletteOAuth2App
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from aspen.api.views.auth import create_user_if_not_exists
from aspen.auth.auth0_management import Auth0Client
from aspen.database.models import User
from aspen.test_infra.models.usergroup import (
    group_factory,
    userrole_factory,
)
from aspen.util.split import SplitClient

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio


async def start_new_transaction(session: AsyncSession):
    await session.commit()
    await session.close()
    session.begin()


async def test_create_new_user_if_not_exists(
    async_session: AsyncSession,
    auth0_apiclient: Auth0Client,
):
    """
    Test creating a new auth0 user on login
    """
    userinfo = {
        "sub": "user123-asdf",
        "org_id": "123456",
        "email": "support@theiagenghi.org",
    }
    group = group_factory(auth0_org_id=userinfo["org_id"])
    async_session.add(group)
    auth0_apiclient.get_org_user_roles.side_effect = [["member"]]  # type: ignore
    await start_new_transaction(async_session)
    await create_user_if_not_exists(async_session, userinfo)
    await start_new_transaction(async_session)
    user = (
        (
            await async_session.execute(
                sa.select(User).filter(  # type: ignore
                    User.auth0_user_id == userinfo["sub"]
                )  # type: ignore
            )
        )
        .scalars()
        .one()
    )
    assert user.email == userinfo["email"]


async def test_dont_create_new_user_if_exists(
    async_session: AsyncSession,
    auth0_apiclient: Auth0Client,
):
    """
    Test creating a new auth0 user on login
    """
    userinfo = {
        "sub": "user123-asdf",
        "org_id": "123456",
        "email": "support@theiagenghi.org",
    }
    group = group_factory(auth0_org_id=userinfo["org_id"])
    user = await userrole_factory(
        async_session, auth0_user_id=userinfo["sub"], group=group
    )
    async_session.add(user)
    await start_new_transaction(async_session)
    await create_user_if_not_exists(async_session, userinfo)
    original_user_id = user.id
    async_session.expire_all()
    await start_new_transaction(async_session)
    db_user = (
        (
            await async_session.execute(
                sa.select(User).filter(  # type: ignore
                    User.auth0_user_id == userinfo["sub"]
                )  # type: ignore
            )
        )
        .scalars()
        .one()
    )
    assert db_user.id == original_user_id


async def test_callback_error_redirects(
    http_client: AsyncClient,
):
    res = await http_client.get(
        "/v2/auth/callback",
        follow_redirects=False,
        params={"error_description": "invitation not found or already used"},
    )
    assert res.status_code == 307
    assert res.is_redirect
    assert "already_accepted" in res.headers["Location"]

    res = await http_client.get(
        "/v2/auth/callback",
        follow_redirects=False,
        params={"error_description": "expired"},
    )
    assert res.status_code == 307
    assert res.is_redirect
    assert "expired" in res.headers["Location"]


async def test_redirect_to_samples_if_exists(
    async_session: AsyncSession,
    auth0_apiclient: Auth0Client,
    auth0_oauth: StarletteOAuth2App,
    http_client: AsyncClient,
    split_client: SplitClient,
):
    """
    Test creating a new auth0 user on login
    """
    userinfo = {
        "sub": "user123-asdf",
        "org_id": "123456",
        "email": "support@theiagenghi.org",
        "name": "Name Goes Here",
    }
    group = group_factory(auth0_org_id=userinfo["org_id"])
    user = await userrole_factory(
        async_session, auth0_user_id=userinfo["sub"], group=group
    )
    async_session.add(user)
    await async_session.commit()

    split_client.get_usergroup_treatment.side_effect = ["control"]  # type: ignore
    auth0_apiclient.get_org_user_roles.side_effect = [["admin"]]  # type: ignore
    auth0_oauth.authorize_access_token.side_effect = [{"userinfo": userinfo}]  # type: ignore
    auth0_apiclient.get_user_orgs.side_effect = [[]]  # type: ignore

    res = await http_client.get(
        "/v2/auth/callback",
        follow_redirects=False,
    )
    assert res.status_code == 307
    assert auth0_apiclient.get_user_orgs.call_count == 0  # type: ignore
    assert res.is_redirect
    assert "data/samples" in res.headers["Location"]


async def test_redirect_to_group_welcome_if_new(
    async_session: AsyncSession,
    auth0_apiclient: Auth0Client,
    auth0_oauth: StarletteOAuth2App,
    http_client: AsyncClient,
    split_client: SplitClient,
):
    """
    Test creating a new auth0 user on login
    """
    userinfo = {
        "sub": "user123-asdf",
        "org_id": "123456",
        "email": "support@theiagenghi.org",
        "name": "Name Goes Here",
    }
    group = group_factory(auth0_org_id=userinfo["org_id"])
    async_session.add(group)
    await async_session.commit()

    split_client.get_usergroup_treatment.side_effect = ["control"]  # type: ignore
    auth0_apiclient.get_org_user_roles.side_effect = [["admin"]]  # type: ignore
    auth0_oauth.authorize_access_token.side_effect = [{"userinfo": userinfo}]  # type: ignore
    auth0_apiclient.get_user_orgs.side_effect = [[]]  # type: ignore

    res = await http_client.get(
        "/v2/auth/callback",
        follow_redirects=False,
    )
    assert res.status_code == 307
    assert res.is_redirect
    assert f"welcome/{group.id}" in res.headers["Location"]
