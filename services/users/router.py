from fastapi import APIRouter, Depends, HTTPException, Response, status

from services.auth import service as auth_service
from services.auth.dependencies import CurrentUser, get_current_user, is_admin
from services.core.errors import EmailAlreadyRegistered
from services.core.security import verify_password
from services.users import service as users_service
from services.users.models import UserCreate, UserInDB, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

# Deliberately does not repeat the address: the caller already knows what they
# typed, and an error body is not the place to echo personal data back.
EMAIL_TAKEN_MESSAGE = "An account with this email address already exists."

# Worded as in `/auth/change-password`, which asks for the same proof.
CURRENT_PASSWORD_REQUIRED_MESSAGE = "Enter your current password to change your email or password."
CURRENT_PASSWORD_INCORRECT_MESSAGE = "Current password is incorrect"


def _to_response(user: UserInDB) -> UserResponse:
    return UserResponse(**user.model_dump(exclude={"hashed_password"}))


def _get_user_or_404(user_id: str) -> UserInDB:
    user = users_service.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user


def _require_self_or_admin(current_user: UserInDB, user_id: str) -> None:
    if current_user.id != user_id and not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only act on your own account",
        )


def _require_current_password(current_user: UserInDB, user_id: str, payload: UserUpdate) -> None:
    """Changing your own email or password takes your current password.

    The session alone is not enough, for the reason `/auth/change-password`
    asks for it: a browser left signed in must not be able to take the account
    over - a new password directly, or a new email and then a reset link.
    Admins are included on their own account; an admin editing someone else
    is exempt, since that account's password is not theirs to know. 400, not
    401: the session is fine, the request is not.
    """
    if current_user.id != user_id or (payload.email is None and payload.password is None):
        return

    if payload.current_password is None:
        detail = CURRENT_PASSWORD_REQUIRED_MESSAGE
    elif not verify_password(payload.current_password, current_user.hashed_password):
        detail = CURRENT_PASSWORD_INCORRECT_MESSAGE
    else:
        return

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _ends_reset_links(before: UserInDB, after: UserInDB, payload: UserUpdate) -> bool:
    """Whether this edit must spend the account's outstanding reset links.

    A reset link is a live credential sitting in the mailbox it was sent to.
    It must die when a new password is set (it would undo it), when the
    address changes (the old mailbox is no longer trusted - often the very
    reason for the change), and when the account is deactivated (reactivating
    it must not revive links requested before). An edit that changes none of
    these leaves a link its owner may be about to use alone.
    """
    return (
        payload.password is not None
        or after.email != before.email
        or (before.is_active and not after.is_active)
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate) -> UserResponse:
    """Public registration. Hashes the password and creates the linked profile.

    The only route in this module that does not require a token.
    """
    try:
        user = users_service.create_user(payload)
    except EmailAlreadyRegistered as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EMAIL_TAKEN_MESSAGE,
        ) from error
    return _to_response(user)


@router.get(
    "",
    response_model=list[UserResponse],
    dependencies=[Depends(get_current_user)],
)
def list_users() -> list[UserResponse]:
    return [_to_response(user) for user in users_service.list_users()]


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(get_current_user)],
)
def get_user(user_id: str) -> UserResponse:
    return _to_response(_get_user_or_404(user_id))


@router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UserUpdate, current_user: CurrentUser) -> UserResponse:
    """Update credential fields. Only the user themselves or an admin may call it.

    `role` and `is_active` are admin-only, so a user cannot promote or
    reactivate themselves by editing their own record. Changing your own email
    or password also takes `current_password`.
    """
    _require_self_or_admin(current_user, user_id)
    before = _get_user_or_404(user_id)

    if not is_admin(current_user):
        restricted = [field for field in ("role", "is_active") if field in payload.model_fields_set]
        if restricted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Only an admin can change: {', '.join(restricted)}",
            )

    _require_current_password(current_user, user_id, payload)

    try:
        updated = users_service.update_user(user_id, payload)
    except EmailAlreadyRegistered as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EMAIL_TAKEN_MESSAGE,
        ) from error

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    if _ends_reset_links(before, updated, payload):
        auth_service.invalidate_outstanding(user_id)

    return _to_response(updated)


@router.delete(
    "/{user_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT
)
def delete_user(user_id: str, current_user: CurrentUser) -> Response:
    """Delete a user and its linked profile. Self or admin only."""
    _require_self_or_admin(current_user, user_id)
    _get_user_or_404(user_id)
    users_service.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
