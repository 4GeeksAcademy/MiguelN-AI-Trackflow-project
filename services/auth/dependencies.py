"""Reusable auth dependencies.

`get_current_user` is the single gate every protected route goes through.
Authentication is stateless: the bearer token is the only credential, there is
no server-side session and no auth cookie.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from services.core.security import decode_access_token
from services.users import service as users_service
from services.users.models import Role, UserInDB

# tokenUrl powers the Authorize button in /docs; it does not affect validation.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> UserInDB:
    """Resolve the caller from `Authorization: Bearer <token>`.

    Any failure along the way - missing, malformed, tampered or expired token,
    or a user that no longer exists - is a 401. A disabled account is a 403:
    the token was valid, the account is not allowed to act.
    """
    claims = decode_access_token(token)
    if claims is None:
        raise _unauthorized("Invalid or expired token")

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise _unauthorized("Token is missing a user id")

    user = users_service.get_user(user_id)
    if user is None:
        raise _unauthorized("User no longer exists")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    return user


CurrentUser = Annotated[UserInDB, Depends(get_current_user)]


def is_admin(user: UserInDB) -> bool:
    return user.role is Role.ADMIN
