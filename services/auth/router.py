import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from services.auth import service as auth_service
from services.auth.dependencies import CurrentUser
from services.auth.emails import send_password_reset_email
from services.auth.models import (
    AuthenticatedUserResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    TokenResponse,
)
from services.core.errors import IncorrectPassword, InvalidResetToken
from services.core.security import (
    access_token_expires_in_seconds,
    create_access_token,
    verify_dummy_password,
    verify_password,
)
from services.profiles import service as profiles_service
from services.profiles.models import ProfilePublicResponse
from services.users import service as users_service
from services.users.models import UserInDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# The one answer `POST /auth/forgot-password` ever gives. Wording it as what the
# server did, not what it found, is what keeps it honest for both outcomes.
FORGOT_PASSWORD_MESSAGE = (
    "If an account exists for that address, a password reset link is on its way."
)

# Every rejected reset token gets this, whichever way it failed. Saying which
# would tell a caller whether a token they did not issue was ever real.
INVALID_RESET_TOKEN_MESSAGE = "This password reset link is invalid or has expired."


def _authenticate(email: str, password: str) -> UserInDB:
    user = users_service.get_user_by_email(email)
    if user is None:
        # Same cost as a real check, so a wrong email is not distinguishable.
        verify_dummy_password(password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    return user


def _issue_token(user: UserInDB) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user_id=user.id, role=user.role.value),
        expires_in=access_token_expires_in_seconds(),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    """Validate email and password, and return a signed JWT access token."""
    return _issue_token(_authenticate(payload.email, payload.password))


@router.post("/token", response_model=TokenResponse)
def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """OAuth2 password-flow variant of `/auth/login`.

    Same credentials, form-encoded, with the email in the `username` field.
    This is the endpoint the Authorize button in `/docs` posts to.
    """
    return _issue_token(_authenticate(form_data.username, form_data.password))


@router.get("/me", response_model=AuthenticatedUserResponse)
def read_current_user(current_user: CurrentUser) -> AuthenticatedUserResponse:
    """Return the authenticated user's credentials plus the linked profile."""
    profile = profiles_service.get_profile_by_user(current_user.id)
    return AuthenticatedUserResponse(
        email=current_user.email,
        role=current_user.role,
        profile=(
            ProfilePublicResponse.model_validate(profile.model_dump())
            if profile is not None
            else None
        ),
    )


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
) -> MessageResponse:
    """Start password recovery for an email address.

    Always `200`, with the same body, whether or not the address has an account.
    Anything else - a `404`, a different message, a noticeably quicker reply -
    turns this route into a way to test which addresses are registered. The
    send is queued behind the response for the same reason: the caller cannot
    time the difference between mailing a link and doing nothing.
    """
    issued = auth_service.request_password_reset(payload.email)
    if issued is not None:
        user, token = issued
        background_tasks.add_task(send_password_reset_email, user.email, token)

    return MessageResponse(message=FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    """Finish password recovery: exchange a reset token for a new password.

    `400` covers every way a token can fail - forged, expired, unknown, or
    already spent - and the token is spent here, so the same link cannot be
    used twice.
    """
    try:
        auth_service.reset_password(payload.token, payload.new_password)
    except InvalidResetToken as error:
        logger.info("Rejected a password reset: token %s", error.reason)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RESET_TOKEN_MESSAGE,
        ) from error

    return MessageResponse(message="Your password has been reset. You can now sign in.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(payload: ChangePasswordRequest, current_user: CurrentUser) -> MessageResponse:
    """Change the password of the signed-in account.

    Requires `Authorization: Bearer <token>` and the current password on top of
    it, so a session left open on a shared machine cannot be used to take the
    account over. A wrong current password is `400`, not `401`: the session is
    fine, the payload is not.
    """
    try:
        auth_service.change_password(
            user=current_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except IncorrectPassword as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        ) from error

    return MessageResponse(message="Your password has been updated.")
