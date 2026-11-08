"""Password recovery and change.

A reset token is a signed JWT, so it carries its own subject and expiry and a
forged or stale one is refused without touching storage. Single use is the one
property a signature cannot give: each token carries a `jti` that is registered
here when issued and stamped used when spent, so replaying a link that already
worked fails even though its signature and `exp` still check out.

The registry stores the `jti` and nothing else, so a leaked database file does
not hand anyone a usable reset link.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from tinydb import Query
from tinydb.table import Table

from services.auth.models import PasswordResetInDB
from services.core.db import get_table
from services.core.errors import IncorrectPassword, InvalidResetToken
from services.core.security import (
    create_password_reset_token,
    decode_password_reset_token,
    verify_password,
)
from services.users import service as users_service
from services.users.models import UserInDB, UserUpdate

logger = logging.getLogger(__name__)

TABLE_NAME = "password_resets"


def _table() -> Table:
    return get_table(TABLE_NAME)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_entry(jti: str) -> PasswordResetInDB | None:
    doc = _table().get(Query().jti == jti)
    return None if doc is None else PasswordResetInDB(**doc)


def purge_expired(now: datetime | None = None) -> int:
    """Drop entries past their expiry. Returns how many were removed.

    Housekeeping only. An expired entry is already unusable - the JWT `exp`
    stops it first - so this just keeps the table from growing forever.
    """
    cutoff = (now or _now()).isoformat()
    removed = _table().remove(Query().expires_at < cutoff)
    return len(removed)


def invalidate_outstanding(user_id: str, now: datetime | None = None) -> int:
    """Spend every unused token for one user. Returns how many were spent.

    Called whenever a password changes, by either route. A reset link that was
    requested and then not used is a live credential for its whole window, so
    the act of setting a new password has to close it.
    """
    entry = Query()
    updated = _table().update(
        {"used_at": (now or _now()).isoformat()},
        (entry.user_id == user_id) & (entry.used_at == None),  # noqa: E711 - TinyDB needs ==
    )
    return len(updated)


def request_password_reset(email: str) -> tuple[UserInDB, str] | None:
    """Issue a reset token for `email`, or None when there is nothing to reset.

    Returning None rather than raising is what keeps user enumeration out of the
    router: an unknown address and a real one differ only in whether there is a
    token to mail, and the caller answers the same way to both.
    """
    user = users_service.get_user_by_email(email)
    if user is None:
        logger.info("Password reset requested for an address with no account")
        return None
    if not user.is_active:
        logger.info("Password reset requested for deactivated user %s", user.id)
        return None

    purge_expired()

    jti = str(uuid4())
    token, expires_at = create_password_reset_token(user_id=user.id, token_id=jti)
    entry = PasswordResetInDB(
        jti=jti,
        user_id=user.id,
        created_at=_now(),
        expires_at=expires_at,
    )
    _table().insert(entry.model_dump(mode="json"))
    logger.info("Issued password reset token %s for user %s", jti, user.id)
    return user, token


def reset_password(token: str, new_password: str) -> UserInDB:
    """Consume a reset token and set the new password.

    Raises `InvalidResetToken` for every way a token can fail: bad signature,
    wrong token type, expiry, an unknown `jti`, one already spent, or a user
    that has since been deleted or deactivated.
    """
    claims = decode_password_reset_token(token)
    if claims is None:
        raise InvalidResetToken("is not valid or has expired")

    jti = claims.get("jti")
    user_id = claims.get("sub")
    if not isinstance(jti, str) or not isinstance(user_id, str) or not jti or not user_id:
        raise InvalidResetToken("is missing its identifiers")

    entry = _get_entry(jti)
    if entry is None:
        # Correctly signed but unknown to the registry: issued against a
        # different database, or already purged well past its expiry.
        raise InvalidResetToken("is not recognised")
    if entry.used_at is not None:
        raise InvalidResetToken("has already been used")
    if entry.user_id != user_id:
        raise InvalidResetToken("does not match the account it was issued for")
    if entry.expires_at <= _now():
        raise InvalidResetToken("has expired")

    user = users_service.get_user(user_id)
    if user is None:
        raise InvalidResetToken("belongs to an account that no longer exists")
    if not user.is_active:
        raise InvalidResetToken("belongs to a deactivated account")

    updated = users_service.update_user(user_id, UserUpdate(password=new_password))
    if updated is None:
        raise InvalidResetToken("belongs to an account that no longer exists")

    # Spends this token along with any sibling still outstanding.
    invalidate_outstanding(user_id)
    logger.info("Password reset completed for user %s with token %s", user_id, jti)
    return updated


def change_password(user: UserInDB, current_password: str, new_password: str) -> UserInDB:
    """Set a new password for an already authenticated user.

    Raises `IncorrectPassword` when the current password does not match. Holding
    a valid session is not enough on its own: a borrowed browser should not be
    able to lock the owner out.
    """
    if not verify_password(current_password, user.hashed_password):
        raise IncorrectPassword()

    updated = users_service.update_user(user.id, UserUpdate(password=new_password))
    if updated is None:
        # The token resolved a moment ago, so this is a race with a deletion.
        raise IncorrectPassword()

    invalidate_outstanding(user.id)
    logger.info("Password changed for user %s", user.id)
    return updated
