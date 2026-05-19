#!/usr/bin/env python3
# create_key.py — dk_* API key management for the DobbyAI proxy.
#
# © 2026 Charitha Jayasuriya — MIT License (see LICENSE).
# Part of dobbyai-proxy-extensions. Standalone: only the stdlib.
#
# The key store is a SQLite file. ONLY the SHA-256 hash of each token is
# stored — the raw `dk_...` value is shown exactly once, at creation, and
# is unrecoverable afterward (this matches auth_middleware.py's validation:
# sha256(token) -> SELECT ... WHERE key_hash = ? AND revoked = 0).
#
# Usage:
#   DOBBYAI_DB_PATH=./dobbyai.db python create_key.py create --name "my-laptop"
#   python create_key.py list
#   python create_key.py revoke --prefix dk_1a2b3c4d
#   python create_key.py revoke --id <uuid>
#
# The DB path resolves the same way auth_middleware.py resolves it:
#   $DOBBYAI_DB_PATH, else ./dobbyai.db   (override with --db)

import argparse
import hashlib
import os
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    key_hash      TEXT UNIQUE NOT NULL,
    key_prefix    TEXT,
    user_id       TEXT,
    name          TEXT,
    created_at    TEXT,
    last_used_at  TEXT,
    revoked       INTEGER DEFAULT 0,
    request_count INTEGER DEFAULT 0
);
"""


def db_path(args) -> str:
    return args.db or os.environ.get("DOBBYAI_DB_PATH", "./dobbyai.db")


def connect(args) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(args))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def cmd_create(args) -> int:
    raw = "dk_" + secrets.token_hex(16)
    conn = connect(args)
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, key_prefix, user_id, name, created_at, revoked, request_count) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (str(uuid.uuid4()), hash_key(raw), raw[:11], args.user,
         args.name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    print(raw)
    print("\n^ Copy this now. Only its hash is stored; it cannot be shown again.",
          file=sys.stderr)
    return 0


def cmd_list(args) -> int:
    conn = connect(args)
    rows = conn.execute(
        "SELECT key_prefix, name, user_id, created_at, last_used_at, "
        "request_count, revoked FROM api_keys ORDER BY created_at"
    ).fetchall()
    conn.close()
    if not rows:
        print("(no keys)")
        return 0
    print(f"{'PREFIX':<13} {'NAME':<20} {'USER':<14} {'REQS':>6}  {'REVOKED':<7} LAST_USED")
    for r in rows:
        print(f"{r['key_prefix'] or '':<13} {r['name'] or '':<20} "
              f"{(r['user_id'] or '')[:14]:<14} {r['request_count']:>6}  "
              f"{'yes' if r['revoked'] else 'no':<7} {r['last_used_at'] or '--'}")
    return 0


def cmd_revoke(args) -> int:
    if not (args.id or args.prefix):
        print("error: pass --id <uuid> or --prefix dk_xxxxxxxx", file=sys.stderr)
        return 2
    conn = connect(args)
    if args.id:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (args.id,))
    else:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE key_prefix = ?",
                           (args.prefix,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    print(f"revoked {n} key(s)" if n else "no matching key")
    return 0 if n else 1


def main() -> int:
    p = argparse.ArgumentParser(description="DobbyAI dk_* key management")
    p.add_argument("--db", help="SQLite path (default: $DOBBYAI_DB_PATH or ./dobbyai.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="mint a new dk_ key (prints it once)")
    c.add_argument("--name", default="", help="human label for the key")
    c.add_argument("--user", default="local", help="owning user id")
    c.set_defaults(func=cmd_create)

    sub.add_parser("list", help="list keys (prefixes only — never the token)").set_defaults(func=cmd_list)

    r = sub.add_parser("revoke", help="revoke a key by id or prefix")
    r.add_argument("--id")
    r.add_argument("--prefix")
    r.set_defaults(func=cmd_revoke)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
