"""Fixtures and helpers for the authentication suite.

Every test in this package calls the service and router functions directly and
asserts what they decided - who gets in, what is stored, what is refused - never
how a response is serialised. These fixtures give each test an empty database
of its own, keep bcrypt fast, and build the users and tokens the cases need.
"""

import base64
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from jose import jwt

from services.auth import router as auth_router
from services.auth import service as auth_service
from services.auth.dependencies import get_current_user
from services.auth.models import LoginRequest, ResetPasswordRequest
from services.core import security
from services.core.config import get_settings
from services.users import service as users_service
from services.users.models import Role, UserCreate, UserInDB, UserUpdate

PASSWORD = "correct-horse-42"
NEW_PASSWORD = "battery-staple-77"

# A fixed instant for tests that move the clock, so expiry arithmetic is exact.
NOON = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_db(db_path):
    """Every auth test starts from an empty database of its own."""
    return db_path


@pytest.fixture(autouse=True)
def fast_bcrypt(request, monkeypatch):
    """bcrypt at cost 4 instead of 12, so the suite runs in seconds.

    The production cost is pinned by the tests marked `real_bcrypt_cost`,
    which opt out of this.
    """
    if request.node.get_closest_marker("real_bcrypt_cost") is None:
        monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)


@pytest.fixture
def make_user() -> Callable[..., UserInDB]:
    """Register a user through the real service, then apply role and status."""

    def make(
        email: str = "ana@trackflow.com",
        password: str = PASSWORD,
        *,
        role: Role = Role.USER,
        is_active: bool = True,
        name: str | None = None,
        phone: str | None = None,
        address: str | None = None,
    ) -> UserInDB:
        user = users_service.create_user(
            UserCreate(email=email, password=password, name=name, phone=phone, address=address)
        )
        if role is not Role.USER or not is_active:
            updated = users_service.update_user(user.id, UserUpdate(role=role, is_active=is_active))
            assert updated is not None
            user = updated
        return user

    return make


@pytest.fixture
def access_token_for() -> Callable[[UserInDB], str]:
    """A real session token, exactly as `/auth/login` would issue it."""

    def issue(user: UserInDB) -> str:
        return security.create_access_token(user_id=user.id, role=user.role.value)

    return issue


@pytest.fixture
def craft_token() -> Callable[..., str]:
    """Sign arbitrary claims: the way to build legacy, forged or malformed tokens."""

    def craft(claims: dict[str, Any], *, key: str | None = None, algorithm: str = "HS256") -> str:
        signing_key = key if key is not None else get_settings().jwt_secret_key
        return jwt.encode(claims, signing_key, algorithm=algorithm)

    return craft


@pytest.fixture
def issue_reset_token() -> Callable[[UserInDB], str]:
    """A real password reset link token, registered as `/auth/forgot-password` does."""

    def issue(user: UserInDB) -> str:
        issued = auth_service.request_password_reset(user.email)
        assert issued is not None, "an active account always gets a reset link"
        return issued[1]

    return issue


# --- Plain helpers ------------------------------------------------------------


def expires_in(seconds: int) -> int:
    """An `exp` claim `seconds` from now."""
    return int((datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp())


def _b64url(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def with_payload(token: str, claims: dict[str, Any]) -> str:
    """Swap a token's payload and keep its original signature: a tampered token."""
    header, _, signature = token.split(".")
    return f"{header}.{_b64url(claims)}.{signature}"


def unsigned_token(claims: dict[str, Any]) -> str:
    """A token declaring `alg: none`, with no signature at all."""
    return f"{_b64url({'alg': 'none', 'typ': 'JWT'})}.{_b64url(claims)}."


def refusal(action: Callable[[], object]) -> HTTPException:
    """Run `action`, which must refuse, and return the refusal."""
    with pytest.raises(HTTPException) as refused:
        action()
    return refused.value


def session_refusal(token: str) -> HTTPException:
    """How the gate in front of every protected route refuses this token."""
    return refusal(lambda: get_current_user(token))


def signs_in(email: str, password: str) -> bool:
    """Whether `/auth/login` would issue a session for these credentials."""
    try:
        auth_router.login(LoginRequest(email=email, password=password))
    except HTTPException:
        return False
    return True


def reset_link_is_dead(link: str) -> bool:
    """Whether `/auth/reset-password` refuses this link.

    Tried with a valid password, so a refusal can only be about the link. A live
    link is spent by the check - fine, since callers assert it is dead.
    """
    request = ResetPasswordRequest(token=link, new_password="a-third-password-9")
    try:
        auth_router.reset_password(request)
    except HTTPException as refused:
        return refused.status_code == 400
    return False
