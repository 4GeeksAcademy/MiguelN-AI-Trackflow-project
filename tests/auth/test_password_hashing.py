"""Password hashing: what every credential check in the API rests on.

The rest of the suite runs bcrypt at a fast test cost; the tests marked
`real_bcrypt_cost` pin what production uses.
"""

import pytest
from passlib.hash import bcrypt

from services.core import security


@pytest.mark.real_bcrypt_cost
def test_passwords_are_hashed_with_bcrypt_at_cost_12_and_a_fresh_salt():
    first = security.hash_password("correct-horse-42")
    second = security.hash_password("correct-horse-42")

    assert first.startswith("$2b$12$")
    assert first != second, "a fresh salt each time"
    assert security.verify_password("correct-horse-42", first)
    assert not security.verify_password("correct-horse-43", first)


@pytest.mark.real_bcrypt_cost
def test_the_dummy_hash_costs_a_full_bcrypt_check(caplog):
    """An unknown email is checked against `DUMMY_HASH` so it takes as long as a
    real account. Were the hash unreadable, the check would return at once and
    that protection would vanish without an error."""
    assert bcrypt.identify(security.DUMMY_HASH)
    assert bcrypt.parsehash(security.DUMMY_HASH)["rounds"] == security.BCRYPT_ROUNDS

    assert security.verify_password("anything-at-all", security.DUMMY_HASH) is False
    assert caplog.text == "", "took the normal path, not the unreadable-hash one"


def test_two_passwords_that_differ_only_after_byte_72_are_not_interchangeable():
    """Older bcrypt silently dropped everything past byte 72; a downgrade must
    not make "the first 72 bytes match" good enough."""
    stored = security.hash_password("a" * 72)

    assert security.verify_password("a" * 72, stored)
    assert not security.verify_password("a" * 72 + "b", stored)


@pytest.mark.parametrize(
    "stored", ["not-a-hash", "$2b$12$truncated", ""], ids=["garbage", "truncated", "empty"]
)
def test_an_unreadable_stored_hash_fails_the_check_and_is_logged(stored, caplog):
    """Corruption must fail closed and stay visible, never raise."""
    assert security.verify_password("correct-horse-42", stored) is False
    assert "could not be read" in caplog.text
