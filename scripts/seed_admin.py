#!/usr/bin/env python3
"""
Promote an existing user to admin, or create a new admin user.

Usage:
  # Promote an existing user by email:
  python scripts/seed_admin.py --email admin@example.com

  # Create a new admin user (if they don't exist yet):
  python scripts/seed_admin.py --email admin@example.com --password "StrongPass123!" --create

  # Revoke admin from a user:
  python scripts/seed_admin.py --email admin@example.com --revoke
"""
import argparse
import hashlib
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings  # noqa: F401 — loads .env
from app.services.database import init_pool, execute, fetchone


def _hash_password(password: str) -> str:
    """Match the argon2 hashing used in auth.py."""
    from argon2 import PasswordHasher
    return PasswordHasher().hash(password)


def promote(email: str) -> None:
    row = fetchone("SELECT id, email, is_admin FROM users WHERE email = %s", (email,))
    if not row:
        print(f"[error] No user found with email: {email}")
        print("        Use --create to create the user first, or register via the app.")
        sys.exit(1)
    if row["is_admin"]:
        print(f"[info]  {email} is already an admin.")
        return
    execute("UPDATE users SET is_admin = TRUE WHERE id = %s", (row["id"],))
    print(f"[ok]    {email} promoted to admin.")


def revoke(email: str) -> None:
    row = fetchone("SELECT id, is_admin FROM users WHERE email = %s", (email,))
    if not row:
        print(f"[error] No user found with email: {email}")
        sys.exit(1)
    execute("UPDATE users SET is_admin = FALSE WHERE id = %s", (row["id"],))
    print(f"[ok]    Admin access revoked for {email}.")


def create_admin(email: str, password: str) -> None:
    existing = fetchone("SELECT id, is_admin FROM users WHERE email = %s", (email,))
    if existing:
        print(f"[info]  User {email} already exists — promoting to admin.")
        execute("UPDATE users SET is_admin = TRUE WHERE id = %s", (existing["id"],))
        print(f"[ok]    {email} promoted to admin.")
        return

    hashed = _hash_password(password)
    user_id = str(uuid.uuid4())
    execute(
        "INSERT INTO users (id, email, password_hash, email_verified, is_admin, trial_used) "
        "VALUES (%s, %s, %s, TRUE, TRUE, TRUE)",
        (user_id, email, hashed),
    )
    print(f"[ok]    Admin user created: {email} (id={user_id})")
    print("        is_verified=TRUE, trial_used=TRUE — log in immediately via the app.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed admin users in CertMind DB")
    parser.add_argument("--email", required=True, help="User email address")
    parser.add_argument("--password", default=None, help="Password (required with --create)")
    parser.add_argument("--create", action="store_true", help="Create the user if they don't exist")
    parser.add_argument("--revoke", action="store_true", help="Remove admin privileges")
    args = parser.parse_args()

    init_pool()

    if args.revoke:
        revoke(args.email)
    elif args.create:
        if not args.password:
            print("[error] --password is required when using --create")
            sys.exit(1)
        create_admin(args.email, args.password)
    else:
        promote(args.email)


if __name__ == "__main__":
    main()
