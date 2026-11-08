"""`get_current_user`: the gate in front of every protected route.

Whether a token is well formed, signed and unexpired is covered in
`test_token.py`. This module covers the account behind a valid token - deleted,
deactivated, demoted - and which routes sit behind the gate at all. Testing that
once here is what lets each endpoint module skip it.
"""

import pytest
from fastapi.routing import APIRoute

from services.auth import router as auth_router
from services.auth.dependencies import get_current_user
from services.inventory.routers import inventory as inventory_router
from services.profiles import router as profiles_router
from services.suppliers import router as suppliers_router
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role, UserUpdate
from tests.auth.conftest import expires_in, refusal, session_refusal

# The routes anyone may call. Registration and sign-in obviously; the two
# password recovery routes because they exist for people who cannot sign in.
PUBLIC_ROUTES = {
    ("POST", "/users"),
    ("POST", "/auth/login"),
    ("POST", "/auth/token"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
}

# Every router that holds identity or commercially sensitive data. The incident
# router is left out on purpose: it is open until the incident panel has a
# login (docs/INCIDENTS.md). A new router with protected data belongs here.
GUARDED_DOMAINS = (
    auth_router.router,
    users_router.router,
    profiles_router.router,
    suppliers_router.router,
    inventory_router.router,
)


def requires_session(route: APIRoute) -> bool:
    """Whether `get_current_user` guards this route, directly or through another dependency."""

    def guarded(dependant) -> bool:
        return any(
            dependency.call is get_current_user or guarded(dependency)
            for dependency in dependant.dependencies
        )

    return guarded(route.dependant)


def test_a_valid_session_resolves_to_the_account_as_it_is_stored_now(make_user, access_token_for):
    """The token names the account; everything else is read fresh on each request."""
    user = make_user(email="ana@trackflow.com")
    token = access_token_for(user)
    users_service.update_user(user.id, UserUpdate(email="ana.ruiz@trackflow.com"))

    caller = get_current_user(token)

    assert caller.id == user.id
    assert caller.email == "ana.ruiz@trackflow.com"


def test_a_session_ends_when_its_account_is_deleted(make_user, access_token_for):
    user = make_user()
    token = access_token_for(user)
    users_service.delete_user(user.id)

    assert session_refusal(token).status_code == 401


def test_a_deactivated_account_is_refused_on_its_next_request(make_user, access_token_for):
    """Deactivation takes effect immediately, not when the token expires. 403, not
    401: the token is fine, the account may not act."""
    user = make_user()
    token = access_token_for(user)
    users_service.update_user(user.id, UserUpdate(is_active=False))

    assert session_refusal(token).status_code == 403


@pytest.mark.parametrize(
    "subject",
    [None, "", 123],
    ids=["missing", "empty", "not a string"],
)
def test_a_signed_token_that_names_no_account_is_refused(subject, make_user, craft_token):
    make_user()
    claims = {"role": "user", "typ": "access", "exp": expires_in(600)}
    if subject is not None:
        claims["sub"] = subject

    assert session_refusal(craft_token(claims)).status_code == 401


def test_a_demoted_admin_cannot_act_as_admin_with_an_older_token(make_user, access_token_for):
    """Permissions come from the stored account, not from the token's `role` claim."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    worker = make_user(email="worker@trackflow.com")
    token = access_token_for(boss)
    users_service.update_user(boss.id, UserUpdate(role=Role.USER))

    caller = get_current_user(token)
    refused = refusal(
        lambda: users_router.update_user(worker.id, UserUpdate(role=Role.MANAGER), caller)
    )

    assert refused.status_code == 403
    assert users_service.get_user(worker.id).role is Role.USER


def test_every_route_except_the_public_ones_requires_a_session():
    """Deleting one `Depends(get_current_user)` would silently open a route.

    The comparison runs both ways: a route that gained protection by mistake
    fails too - locking the recovery routes would strand anyone who forgot
    their password.
    """
    routes = [route for domain in GUARDED_DOMAINS for route in domain.routes]
    unguarded = {
        (method, route.path)
        for route in routes
        if isinstance(route, APIRoute) and not requires_session(route)
        for method in route.methods
    }

    assert len(routes) > len(PUBLIC_ROUTES), "the routers were read"
    assert unguarded == PUBLIC_ROUTES
