"""`GET /profiles/me`: the signed-in account's own profile.

Scoped to the caller by construction - there is no id in the request to
tamper with - so the decisions are whose profile comes back and what happens
when there is none.
"""

from services.auth.dependencies import get_current_user
from services.profiles import router as profiles_router
from services.profiles import service as profiles_service
from tests.auth.conftest import refusal


def my_profile(token: str):
    """The route as FastAPI runs it: the gate first, then the handler."""
    return profiles_router.read_my_profile(get_current_user(token))


def test_returns_the_callers_own_profile(make_user, access_token_for):
    ana = make_user(email="ana@trackflow.com", name="Ana Ruiz", address="Monterrey")

    profile = my_profile(access_token_for(ana))

    assert profile.user_id == ana.id
    assert (profile.name, profile.address) == ("Ana Ruiz", "Monterrey")


def test_each_account_only_ever_sees_its_own_profile(make_user, access_token_for):
    ana = make_user(email="ana@trackflow.com", name="Ana")
    ben = make_user(email="ben@trackflow.com", name="Ben")

    assert my_profile(access_token_for(ana)).name == "Ana"
    assert my_profile(access_token_for(ben)).name == "Ben"


def test_an_account_without_a_profile_row_gets_not_found(make_user, access_token_for):
    """The API always creates one, but a row edited or restored by hand may lack it."""
    ana = make_user()
    profiles_service.delete_profile_by_user(ana.id)

    refused = refusal(lambda: my_profile(access_token_for(ana)))

    assert refused.status_code == 404
    assert refused.detail == "No profile is linked to this account"
