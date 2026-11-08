"""`PUT /profiles/me`: editing the signed-in account's name, phone and address.

Only the fields sent change, an explicit null erases one, and nothing in the
body can point the edit at somebody else's profile.
"""

import pytest
from pydantic import ValidationError

from services.auth.dependencies import get_current_user
from services.profiles import router as profiles_router
from services.profiles import service as profiles_service
from services.profiles.models import ProfileUpdate
from tests.auth.conftest import refusal


def update_my_profile(token: str, **fields):
    """The route as FastAPI runs it: the gate first, then the handler."""
    return profiles_router.update_my_profile(ProfileUpdate(**fields), get_current_user(token))


def test_a_body_naming_another_profile_still_edits_only_the_callers(make_user, access_token_for):
    """Changing an id in the request must not reach somebody else's profile."""
    ana = make_user(email="ana@trackflow.com", name="Ana")
    ben = make_user(email="ben@trackflow.com", name="Ben")
    bens_profile = profiles_service.get_profile_by_user(ben.id)

    updated = update_my_profile(
        access_token_for(ana), name="Hijacked", user_id=ben.id, id=bens_profile.id
    )

    assert "user_id" not in updated.model_dump()
    assert profiles_service.get_profile_by_user(ana.id).user_id == ana.id
    assert profiles_service.get_profile_by_user(ben.id).name == "Ben"
    assert profiles_service.get_profile_by_user(ana.id).name == "Hijacked"


def test_only_the_fields_sent_change(make_user, access_token_for):
    ana = make_user(name="Ana Ruiz", phone="+52 81 5555 0000", address="Monterrey")

    updated = update_my_profile(access_token_for(ana), phone="+34 976 000 000")

    assert (updated.name, updated.phone, updated.address) == (
        "Ana Ruiz",
        "+34 976 000 000",
        "Monterrey",
    )


def test_an_explicit_null_erases_a_field(make_user, access_token_for):
    """How the UI clears an input."""
    ana = make_user(name="Ana Ruiz", phone="+52 81 5555 0000")

    updated = update_my_profile(access_token_for(ana), phone=None)

    assert (updated.name, updated.phone) == ("Ana Ruiz", None)


def test_an_empty_body_changes_nothing(make_user, access_token_for):
    ana = make_user(name="Ana Ruiz")

    assert update_my_profile(access_token_for(ana)).name == "Ana Ruiz"


@pytest.mark.parametrize("field, limit", [("name", 120), ("phone", 40), ("address", 255)])
def test_each_field_is_capped_at_its_column_size(field, limit):
    ProfileUpdate(**{field: "x" * limit})

    with pytest.raises(ValidationError) as refused:
        ProfileUpdate(**{field: "x" * (limit + 1)})

    assert refused.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_blank_text_is_stored_as_sent(value, make_user, access_token_for):
    """Pinned, not endorsed: the UI sends null for a cleared field, but the API
    does not normalise (TESTING.md, open questions)."""
    ana = make_user(name="Ana Ruiz")

    assert update_my_profile(access_token_for(ana), name=value).name == value


def test_an_account_without_a_profile_row_gets_not_found(make_user, access_token_for):
    ana = make_user()
    profiles_service.delete_profile_by_user(ana.id)

    assert refusal(lambda: update_my_profile(access_token_for(ana), name="Ana")).status_code == 404


def test_a_profile_that_disappears_mid_request_gets_not_found(
    make_user, access_token_for, monkeypatch
):
    ana = make_user()
    token = access_token_for(ana)

    with monkeypatch.context() as patch:
        patch.setattr(profiles_service, "update_profile_by_user", lambda *_args: None)
        refused = refusal(lambda: update_my_profile(token, name="Ana"))

    assert refused.status_code == 404
