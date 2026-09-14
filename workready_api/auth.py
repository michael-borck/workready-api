"""Student sessions and request-wide privacy boundaries.

Contractor codes are enrolment credentials, exchanged once through POST login.
Only hashes of short-lived session tokens are stored. Every private route uses
the same authentication and ownership policy, including read-only lookups.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from workready_api import db

_student: ContextVar[dict | None] = ContextVar("student_session", default=None)
_attempts: OrderedDict[str, deque] = OrderedDict()
_lock = Lock()
MAX_REQUEST_BYTES = 6 * 1024 * 1024


def authenticated_student() -> dict:
    student = _student.get()
    if student is None:
        raise HTTPException(401, "Please sign in with your access code.")
    return student


def init_sessions() -> None:
    with db.get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS student_sessions (
            token_hash TEXT PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        )""")
        conn.execute("DELETE FROM student_sessions WHERE expires_at <= ?", (db._now(),))
        for row in conn.execute('SELECT id, code, handle FROM students').fetchall():
            if row['handle'].split('@')[0] == row['code'].replace('-', '').lower():
                conn.execute('UPDATE students SET handle=? WHERE id=?', (db.make_handle(row['code']), row['id']))


def request_budget(request: Request, bucket: str, maximum: int) -> None:
    """Bounded process-local sliding window, checked before expensive work.

Use a shared reverse-proxy limiter as well when running multiple workers.
Do not read X-Forwarded-For here; trusted proxy handling belongs to uvicorn.
"""
    key = bucket + ":" + (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _lock:
        queue = _attempts.setdefault(key, deque())
        _attempts.move_to_end(key)
        while queue and queue[0] <= now - 60:
            queue.popleft()
        if len(queue) >= maximum:
            raise HTTPException(429, "Too many requests. Please wait a minute.", headers={"Retry-After": "60"})
        queue.append(now)
        while len(_attempts) > 10000:
            _attempts.popitem(last=False)


def resolve_session(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or len(header) > 256:
        raise HTTPException(401, "Please sign in again.")
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    with db.get_db() as conn:
        row = conn.execute("""SELECT s.* FROM student_sessions session
            JOIN students s ON s.id = session.student_id
            JOIN codes c ON c.code = s.code
            WHERE session.token_hash = ? AND session.expires_at > ? AND c.active = 1""",
            (digest, db._now())).fetchone()
    if not row:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    request.state.session_hash = digest
    return dict(row)


def assert_owner(field: str, identifier, student_id: int) -> None:
    # SQL fragments are constants; user input is only ever a bound parameter.
    queries = {
        "application_id": "SELECT student_id FROM applications WHERE id = ?",
        "task_id": "SELECT a.student_id FROM tasks t JOIN applications a ON a.id=t.application_id WHERE t.id=?",
        "event_id": "SELECT a.student_id FROM calendar_events e JOIN applications a ON a.id=e.application_id WHERE e.id=?",
        "message_id": "SELECT student_id FROM messages WHERE id=?",
    }
    if field not in queries:
        raise HTTPException(404, "Not found")
    with db.get_db() as conn:
        row = conn.execute(queries[field], (identifier,)).fetchone()
    if row is None or row[0] != student_id:
        raise HTTPException(404, "Not found")


def assert_open_placement(application_id: int) -> None:
    application = db.get_application(application_id)
    if not application or application.get('status') not in ('active', 'hired'):
        raise HTTPException(409, 'This placement is closed. Its records are read-only.')


class StudentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            path = request.url.path
            public = (path == "/health" or path == "/api/v1/postings" or path == "/api/v1/privacy"
                      or path.startswith("/api/v1/jobs/"))
            if public and not request.headers.get("authorization"):
                return await handler(request)
            student = resolve_session(request)
            context = _student.set(student)
            try:
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    request_budget(request, "student-write:" + str(student["id"]), 60)
                identifiers = dict(request.path_params)
                if "application/json" in request.headers.get("content-type", ""):
                    try:
                        payload = await request.json()
                    except ValueError:
                        raise HTTPException(400, "Invalid JSON")
                    if isinstance(payload, dict):
                        from workready_api.pdf import redact_contact_details
                        payload = {k: redact_contact_details(v) if k in ('message', 'content', 'subject', 'body') and isinstance(v, str) else v for k, v in payload.items()}
                        request._json = payload
                        request._body = json.dumps(payload).encode()
                        for field in ('application_id', 'session_id'):
                            if field in payload and field in identifiers and str(payload[field]) != str(identifiers[field]):
                                raise HTTPException(404, 'Not found')
                        identifiers.update({k: v for k, v in payload.items() if k in ("application_id", "session_id")})
                for field in ("application_id", "task_id", "event_id", "message_id"):
                    if field in identifiers:
                        assert_owner(field, identifiers[field], student["id"])
                        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                            if field == 'application_id':
                                assert_open_placement(identifiers[field])
                            elif field == 'task_id':
                                assert_open_placement(db.get_task(identifiers[field])['application_id'])
                            elif field == 'event_id':
                                assert_open_placement(db.get_calendar_event(identifiers[field])['application_id'])
                if "session_id" in identifiers:
                    sid = identifiers["session_id"]
                    if "/lunchroom/" in path:
                        session = db.get_lunchroom_session(sid)
                    else:
                        session = db.get_interview_session(sid)
                        kind = "exit" if "/exit/" in path else "performance_review" if "/perf-review/" in path else "hiring"
                        if session and session.get("kind") != kind:
                            raise HTTPException(404, "Session not found")
                    if session is None:
                        raise HTTPException(404, "Session not found")
                    assert_owner("application_id", session["application_id"], student["id"])
                    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                        assert_open_placement(session['application_id'])
                if "thread_id" in identifiers:
                    # Thread queries themselves must be scoped by student_id.
                    messages = db.get_thread(identifiers["thread_id"], student["id"])
                    if not messages:
                        raise HTTPException(404, "Thread not found")
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return response
            finally:
                _student.reset(context)
        return guarded


class RequestBoundary:
    """Bound request bodies, suppress private-response caching and reject old URLs."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path.startswith("/api/v1/student/") or path.startswith("/api/v1/mail/sent/") or path.startswith("/api/v1/inbox/WR-"):
            return await JSONResponse({"detail": "Use session login and /api/v1/me routes."}, status_code=410)(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
        if declared > MAX_REQUEST_BYTES:
            return await JSONResponse({"detail": "Upload too large. Maximum request size is 6 MB."}, status_code=413)(scope, receive, send)
        # Reject unauthenticated student uploads before buffering their bodies.
        public = path in ('/api/v1/auth/login', '/api/v1/postings', '/api/v1/privacy') or path.startswith(('/api/v1/admin/', '/api/v1/jobs/'))
        if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and path.startswith('/api/v1/') and not public:
            try:
                resolve_session(Request(scope))
            except HTTPException as error:
                return await JSONResponse({'detail': error.detail}, status_code=error.status_code, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        body = bytearray()
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_REQUEST_BYTES:
                    return await JSONResponse({"detail": "Upload too large"}, status_code=413)(scope, receive, send)
                if not message.get("more_body"):
                    break
        consumed = False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def private_send(message):
            if message["type"] == "http.response.start" and path.startswith("/api/"):
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(message)
        await self.app(scope, replay if scope["method"] not in ("GET", "HEAD", "OPTIONS") else receive, private_send)


router = APIRouter(prefix="/api/v1/auth")

class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/login")
def login(request: Request, payload: LoginRequest):
    request_budget(request, "login", int(os.environ.get("LOGIN_REQUESTS_PER_MINUTE", "10")))
    student = db.get_or_create_student(payload.code)
    if not student:
        raise HTTPException(401, "Unknown or inactive access code. Please check your code.")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=int(os.environ.get("STUDENT_SESSION_HOURS", "8")))
    with db.get_db() as conn:
        conn.execute("DELETE FROM student_sessions WHERE expires_at <= ?", (db._now(),))
        conn.execute("INSERT INTO student_sessions VALUES (?, ?, ?)",
                     (hashlib.sha256(token.encode()).hexdigest(), student["id"], expires.isoformat()))
    return JSONResponse({"token": token, "expires_at": expires.isoformat()}, headers={"Cache-Control": "no-store"})


@router.post("/logout")
def logout(request: Request):
    resolve_session(request)
    with db.get_db() as conn:
        conn.execute("DELETE FROM student_sessions WHERE token_hash = ?", (request.state.session_hash,))
    return JSONResponse({"status": "signed_out"}, headers={"Cache-Control": "no-store"})
