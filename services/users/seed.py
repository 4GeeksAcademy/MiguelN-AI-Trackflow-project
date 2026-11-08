"""Bootstrap a privileged account.

`POST /users` always creates a plain `user`, by design. The first admin (and any
manager) has to be created out of band, which is what this script is for.

    uv run seed-user --email admin@trackflow.com --role admin
"""

import argparse
import getpass
import json
import sys
from typing import NoReturn

from pydantic import ValidationError

from services.core.db import get_db_path
from services.core.errors import EmailAlreadyRegistered
from services.users import service as users_service
from services.users.models import Role, UserCreate, UserUpdate

# What reading or writing the TinyDB file can raise: the file is missing its
# permissions or its disk, or it is not valid JSON any more.
STORAGE_ERRORS = (OSError, json.JSONDecodeError)


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a TrackFlow user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="Prompted for if omitted.")
    parser.add_argument(
        "--role",
        default=Role.ADMIN.value,
        choices=[role.value for role in Role],
    )
    parser.add_argument("--name", default=None, help="Display name for the linked profile.")
    args = parser.parse_args()

    role = Role(args.role)
    db_path = get_db_path()

    try:
        password = args.password or getpass.getpass("Password: ")
    except EOFError:
        _fail("no password was given and there is no terminal to ask for one. Pass --password.")

    try:
        existing = users_service.get_user_by_email(args.email)
    except STORAGE_ERRORS as error:
        _fail(
            f"the user database at {db_path} could not be read ({type(error).__name__}). "
            "Nothing was changed."
        )

    if existing is not None:
        if existing.role is role:
            print(f"{existing.email} already exists with role {role.value}. Nothing to do.")
            return
        try:
            promoted = users_service.update_user(existing.id, UserUpdate(role=role))
        except STORAGE_ERRORS as error:
            _fail(
                f"could not write to the user database at {db_path} ({type(error).__name__}). "
                f"{existing.email} keeps role {existing.role.value}."
            )
        if promoted is None:
            _fail(
                f"{existing.email} was deleted before its role could change. Nothing was changed."
            )
        print(f"Promoted {existing.email} from {existing.role.value} to {role.value}.")
        return

    try:
        payload = UserCreate(email=args.email, password=password, name=args.name)
    except ValidationError as error:
        # `include_input=False`: the rejected value may be the password itself.
        for problem in error.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in problem["loc"]) or "input"
            print(f"error: {field}: {problem['msg']}", file=sys.stderr)
        raise SystemExit(1) from error

    try:
        user = users_service.create_user(payload)
    except EmailAlreadyRegistered as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except STORAGE_ERRORS as error:
        _fail(
            f"could not write to the user database at {db_path} ({type(error).__name__}). "
            "No account was created."
        )

    try:
        updated = users_service.update_user(user.id, UserUpdate(role=role))
    except STORAGE_ERRORS:
        updated = None
    if updated is None:
        _fail(
            f"created {user.email} (id {user.id}) as a plain user, but could not give it role "
            f"{role.value}. Run this command again to promote it."
        )
    print(f"Created {user.email} with role {role.value} (id {user.id}).")


if __name__ == "__main__":
    main()
