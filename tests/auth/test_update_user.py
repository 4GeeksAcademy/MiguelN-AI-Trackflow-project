"""`PUT /users/{user_id}`: editing an account's credentials.

Self or admin only, with `role` and `is_active` admin-only. The policy decided
for BUG-3: changing the email or password of your *own* account - admins
included - needs your current password, as `/auth/change-password` already
does; otherwise a borrowed session could take the account over. An admin
editing someone else needs no password. Any password set here spends that
account's outstanding reset links, like the two `/auth` password routes.
"""

import json

import pytest
from pydantic import ValidationError
from tinydb import Query

from services.core.db import get_table
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role, UserUpdate
from tests.auth.conftest import (
    NEW_PASSWORD,
    PASSWORD,
    refusal,
    reset_link_is_dead,
    session_refusal,
    signs_in,
)


def update(target_id: str, caller, **changes):
    return users_router.update_user(target_id, UserUpdate(**changes), caller)


def update_refusal(target_id: str, caller, **changes):
    return refusal(lambda: update(target_id, caller, **changes))


# --- Who may edit whom -----------------------------------------------------------


@pytest.mark.parametrize("target", ["another account", "an id that does not exist"])
def test_a_non_admin_cannot_edit_anyone_else(target, make_user):
    """403 even for a missing id, so ids cannot be probed."""
    ana = make_user(email="ana@trackflow.com")
    ben = make_user(email="ben@trackflow.com")
    target_id = ben.id if target == "another account" else "no-such-id"

    refused = update_refusal(target_id, ana, email="taken@trackflow.com", current_password=PASSWORD)

    assert refused.status_code == 403
    assert users_service.get_user(ben.id).email == "ben@trackflow.com"


# --- BUG-3: your own email or password needs your current password ------------------


@pytest.mark.parametrize(
    "field, value", [("email", "new@trackflow.com"), ("password", NEW_PASSWORD)]
)
@pytest.mark.parametrize("proof", [None, "not-the-password"], ids=["no proof", "wrong proof"])
def test_your_own_email_or_password_needs_your_current_password(field, value, proof, make_user):
    ana = make_user(email="ana@trackflow.com")
    changes = {field: value}
    if proof is not None:
        changes["current_password"] = proof

    refused = update_refusal(ana.id, ana, **changes)

    assert refused.status_code == 400
    assert signs_in("ana@trackflow.com", PASSWORD), "neither the email nor the password changed"


def test_a_wrong_current_password_reads_like_the_change_password_route(make_user):
    ana = make_user(email="ana@trackflow.com")

    refused = update_refusal(ana.id, ana, password=NEW_PASSWORD, current_password="nope-nope")

    assert refused.detail == "Current password is incorrect"


def test_an_admin_editing_their_own_account_needs_their_current_password_too(make_user):
    """Admin sessions are the most valuable to borrow."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)

    refused = update_refusal(boss.id, boss, password=NEW_PASSWORD)

    assert refused.status_code == 400
    assert signs_in("boss@trackflow.com", PASSWORD)


def test_with_the_right_current_password_your_own_change_is_applied(make_user):
    ana = make_user(email="ana@trackflow.com")

    update(
        ana.id,
        ana,
        email="Ana.Ruiz@TrackFlow.com",
        password=NEW_PASSWORD,
        current_password=PASSWORD,
    )

    assert signs_in("ana.ruiz@trackflow.com", NEW_PASSWORD)
    assert not signs_in("ana@trackflow.com", NEW_PASSWORD), "the old address is gone"
    row = get_table(users_service.TABLE_NAME).get(Query().id == ana.id)
    assert "current_password" not in row
    assert PASSWORD not in json.dumps(row), "the proof is never stored"


@pytest.mark.parametrize("editor", ["the account itself", "an admin"])
def test_a_password_set_here_spends_that_accounts_reset_links(editor, make_user, issue_reset_token):
    """Same rule as `/auth/change-password` and `/auth/reset-password`: a pending
    link would otherwise undo the change."""
    ana = make_user(email="ana@trackflow.com")
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    link = issue_reset_token(ana)

    if editor == "the account itself":
        update(ana.id, ana, password=NEW_PASSWORD, current_password=PASSWORD)
    else:
        update(ana.id, boss, password=NEW_PASSWORD)

    assert reset_link_is_dead(link)
    assert signs_in("ana@trackflow.com", NEW_PASSWORD)


@pytest.mark.parametrize("change", ["address moved", "deactivated, then reactivated"])
def test_moving_or_deactivating_an_account_spends_its_reset_links(
    change, make_user, issue_reset_token
):
    """BUG-6. A reset link lives in the mailbox it was sent to. Moving the account
    to a new address - often done precisely because the old mailbox is no longer
    trusted - must not leave a working link there; nor may reactivating an
    account revive links requested before it was deactivated."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)

    if change == "address moved":
        update(ana.id, boss, email="ana.new@trackflow.com")
        email_now = "ana.new@trackflow.com"
    else:
        update(ana.id, boss, is_active=False)
        update(ana.id, boss, is_active=True)
        email_now = "ana@trackflow.com"

    assert reset_link_is_dead(link)
    assert signs_in(email_now, PASSWORD), "the password itself was not touched"


def test_an_edit_that_changes_nothing_leaves_reset_links_alone(make_user, issue_reset_token):
    """Re-saving the same address, or a role change, is no reason to kill a link
    the owner may be about to use."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)

    update(ana.id, boss, email="ANA@trackflow.com", role=Role.MANAGER)

    assert not reset_link_is_dead(link), "the link still works"
    assert signs_in("ana@trackflow.com", "a-third-password-9"), "and was just used"


# --- Admin-only fields --------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, named",
    [
        ({"role": Role.ADMIN}, "role"),
        ({"is_active": True}, "is_active"),
        ({"role": None}, "role"),
        ({"role": Role.MANAGER, "is_active": False}, "role, is_active"),
    ],
    ids=["promote", "reactivate", "explicit null", "both"],
)
def test_only_an_admin_can_change_role_or_status(changes, named, make_user):
    ana = make_user(email="ana@trackflow.com")

    refused = update_refusal(ana.id, ana, **changes)

    assert refused.status_code == 403
    assert named in refused.detail, "the refusal names the fields"
    stored = users_service.get_user(ana.id)
    assert (stored.role, stored.is_active) == (Role.USER, True)


def test_an_admin_manages_another_account_without_its_password(make_user, access_token_for):
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    ana_session = access_token_for(ana)

    updated = update(
        ana.id, boss, email="ana.ruiz@trackflow.com", role=Role.MANAGER, is_active=False
    )

    assert (updated.email, updated.role, updated.is_active) == (
        "ana.ruiz@trackflow.com",
        Role.MANAGER,
        False,
    )
    assert session_refusal(ana_session).status_code == 403, "deactivation is immediate"


# --- Duplicates and partial writes ---------------------------------------------------


def test_an_email_already_in_use_is_refused(make_user):
    ana = make_user(email="ana@trackflow.com")
    make_user(email="ben@trackflow.com")

    refused = update_refusal(ana.id, ana, email="BEN@trackflow.com", current_password=PASSWORD)

    assert refused.status_code == 409
    assert users_service.get_user(ana.id).email == "ana@trackflow.com"


def test_your_own_email_in_another_case_is_not_a_conflict(make_user):
    ana = make_user(email="ana@trackflow.com")

    updated = update(ana.id, ana, email="ANA@TrackFlow.com", current_password=PASSWORD)

    assert updated.email == "ana@trackflow.com"


def test_a_conflicting_email_blocks_the_whole_edit(make_user):
    """No partial write: the password in the same request is not applied either."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    make_user(email="ben@trackflow.com")

    refused = update_refusal(ana.id, boss, email="ben@trackflow.com", password=NEW_PASSWORD)

    assert refused.status_code == 409
    assert signs_in("ana@trackflow.com", PASSWORD)


@pytest.mark.parametrize(
    "changes", [{}, {"email": None, "password": None}], ids=["empty body", "explicit nulls"]
)
def test_an_empty_edit_changes_nothing_and_needs_no_password(changes, make_user):
    ana = make_user(email="ana@trackflow.com")

    assert update(ana.id, ana, **changes).email == "ana@trackflow.com"
    assert signs_in("ana@trackflow.com", PASSWORD)


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"role": "superadmin"}, "role"),
        ({"email": "not-an-email"}, "email"),
        ({"password": "short7!"}, "password"),
        ({"password": "Contraseña" + "x" * 62}, "password"),
    ],
    ids=["unknown role", "malformed email", "7-character password", "73-byte password"],
)
def test_an_invalid_value_is_refused_by_validation(changes, field):
    with pytest.raises(ValidationError) as refused:
        UserUpdate(**changes)

    assert refused.value.errors()[0]["loc"] == (field,)


# --- Accounts that are not there -------------------------------------------------------


def test_an_admin_editing_an_unknown_account_gets_not_found(make_user):
    boss = make_user(role=Role.ADMIN)

    assert update_refusal("no-such-id", boss, role=Role.MANAGER).status_code == 404


def test_an_account_deleted_mid_request_is_not_found(make_user, monkeypatch):
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    real_update = users_service.update_user

    def deleted_first(user_id, payload):
        users_service.delete_user(user_id)
        return real_update(user_id, payload)

    with monkeypatch.context() as patch:
        patch.setattr(users_service, "update_user", deleted_first)
        refused = update_refusal(ana.id, boss, role=Role.MANAGER)

    assert refused.status_code == 404


@pytest.mark.parametrize("changes", [{"role": Role.USER}, {"is_active": False}])
def test_an_admin_can_demote_or_deactivate_the_last_admin(changes, make_user):
    """Pinned, not endorsed: this can lock everyone out (TESTING.md, open questions)."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)

    updated = update(boss.id, boss, **changes)

    assert (updated.role is Role.USER) or (updated.is_active is False)
