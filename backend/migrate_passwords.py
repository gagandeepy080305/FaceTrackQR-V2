"""
One-off migration: hash any faculty passwords still stored as plain text.

Run from the backend folder (with the virtualenv active):

    python migrate_passwords.py

Safe to run repeatedly; already-hashed passwords are left alone. Plain-text
passwords are also upgraded automatically the next time that faculty logs in.
"""

from werkzeug.security import generate_password_hash
from database.mongodb import db

HASH_PREFIXES = ("pbkdf2:", "scrypt:")
HASH_METHOD = "pbkdf2:sha256"  # works on every Python build


def hash_password(password):
    return generate_password_hash(password, method=HASH_METHOD)

updated = 0

for faculty in db.faculty.find({}):
    stored = faculty.get("password")

    if isinstance(stored, str) and stored and not stored.startswith(HASH_PREFIXES):
        db.faculty.update_one(
            {"_id": faculty["_id"]},
            {"$set": {"password": hash_password(stored)}}
        )
        updated += 1

print(f"Hashed {updated} plain-text password(s).")
