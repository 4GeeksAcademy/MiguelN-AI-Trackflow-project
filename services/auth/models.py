from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from services.profiles.models import ProfilePublicResponse
from services.users.models import Password, Role, normalize_email


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthenticatedUserResponse(BaseModel):
    """`GET /auth/me`: session display fields and the linked public profile."""

    email: EmailStr
    role: Role
    profile: ProfilePublicResponse | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        return normalize_email(value)


class ResetPasswordRequest(BaseModel):
    """The token comes from the emailed link; the password is the new one."""

    token: str = Field(min_length=1)
    new_password: Password


class ChangePasswordRequest(BaseModel):
    """`current_password` has no length floor: it is checked against the stored
    hash, and an account created before today's rules must still be able to
    change away from a short password."""

    current_password: str = Field(min_length=1)
    new_password: Password


class MessageResponse(BaseModel):
    """A human-readable outcome for routes that have nothing else to return."""

    message: str


class PasswordResetInDB(BaseModel):
    """Registry entry for one issued reset token.

    Only the `jti` is kept, never the token itself, so this table cannot be read
    back into a working reset link. It exists solely to make a token single-use:
    the signature and `exp` already cover authenticity and expiry.
    """

    jti: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
