"""DobbyAI dk_* token auth middleware for claude-code-proxy."""
import hashlib
import sqlite3
import os
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

DB_PATH = os.environ.get("DOBBYAI_DB_PATH", "./dobbyai.db")

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def validate_dk_token(raw_key: str) -> bool:
    if not raw_key.startswith("dk_"):
        return False
    h = hash_key(raw_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (h,)
        ).fetchone()
        if row:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE api_keys SET last_used_at = ?, request_count = request_count + 1 WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()
        conn.close()
        return row is not None
    except Exception:
        return False

class DobbyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/", "/health"):
            return await call_next(request)

        api_key = request.headers.get("x-api-key") or ""
        auth_header = request.headers.get("authorization") or ""

        token = None
        if api_key.startswith("dk_"):
            token = api_key
        elif auth_header.startswith("Bearer dk_"):
            token = auth_header[7:]

        if not token:
            return JSONResponse(status_code=401, content={"error": "Missing or invalid dk_* API key"})

        if not validate_dk_token(token):
            return JSONResponse(status_code=401, content={"error": "Invalid or revoked API key"})

        return await call_next(request)
