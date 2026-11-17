"""Create or update a local account using an interactively entered password."""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import re
import tempfile

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--file", default="private/users.json")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.username):
        parser.error("Use 1–64 letters, digits, dots, underscores or dashes.")
    password = getpass("Password (at least 12 characters): ")
    if not 12 <= len(password) <= 256 or password != getpass("Confirm password: "):
        parser.error("Passwords must match and contain 12–256 characters.")
    path = Path(args.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    users = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    users[args.username] = generate_password_hash(password)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(users, handle, indent=2)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)
    print(f"Saved account {args.username} to {path}.")


if __name__ == "__main__":
    main()
