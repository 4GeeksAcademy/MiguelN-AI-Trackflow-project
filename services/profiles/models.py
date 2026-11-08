from pydantic import BaseModel, Field


class ProfileBase(BaseModel):
    """Display name and contact data. These fields live here, never on `User`."""

    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=255)


class ProfileUpdate(ProfileBase):
    """Omitted fields are left untouched; send an explicit null to clear one."""


class ProfileInDB(ProfileBase):
    id: str
    user_id: str


class ProfileResponse(ProfileBase):
    id: str
    user_id: str


class ProfilePublicResponse(ProfileBase):
    id: str
