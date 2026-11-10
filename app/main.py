"""
Vanish: burn-after-reading encrypted notes. One Flask app, one SQLite file.

Pages
  GET  /                         write a note
  GET  /n/<id>                   read a note (the key is in the #fragment, never sent here)
  GET  /about                    how it works
API
  GET    /api/health             liveness check (used by the Docker HEALTHCHECK)
  POST   /api/notes              create a note: the browser sends ciphertext only
  GET    /api/notes/<id>         views left + expiry; does NOT use up a view
  POST   /api/notes/<id>/open    atomically use one view and return the ciphertext
  DELETE /api/notes/<id>         destroy early (needs the delete token from creation)

The server never sees a note's text: it is encrypted in the browser with AES-256-GCM and the key
travels only in the part of the link after '#', which browsers never send to servers.
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque

from flask import Flask, Response, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
PAGES_DIR = os.path.join(HERE, "pages")

DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "..", "vanish.db"))
MAX_CIPHERTEXT_CHARS = 16000              # ~4,000 characters of text after encryption + base64
ALLOWED_TTLS = {300, 3600, 86400, 604800}  # 5 min, 1 hour, 1 day, 7 days
MAX_VIEWS_LIMIT = 10
CREATES_PER_MINUTE = int(os.environ.get("CREATES_PER_MINUTE", "20"))  # per client IP
CLEANUP_EVERY_SECONDS = 60

ID_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id                TEXT PRIMARY KEY,
    ciphertext        TEXT    NOT NULL,
    iv                TEXT    NOT NULL,
    protected         INTEGER NOT NULL,
    max_views         INTEGER NOT NULL,
    view_count        INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    expires_at        INTEGER NOT NULL,
    delete_token_hash TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_expires_at ON notes (expires_at);
"""

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
# Caddy sits in front and adds X-Forwarded-For / -Proto; trust exactly one proxy hop.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)


# ------------------------------------------------------------------ database

def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.close()


def delete_expired():
    conn = connect()
    removed = conn.execute("DELETE FROM notes WHERE expires_at <= ?", (int(time.time()),)).rowcount
    conn.close()
    if removed:
        log("expired_notes_deleted", count=removed)
    return removed


def _cleanup_loop():
    while True:
        time.sleep(CLEANUP_EVERY_SECONDS)
        try:
            delete_expired()
        except Exception as e:  # keep the thread alive no matter what
            log("cleanup_failed", error=str(e))


# ------------------------------------------------------------------ helpers

def log(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def err(status, code, message):
    return jsonify({"error": code, "message": message}), status


GONE = "This note doesn't exist, has expired, or has already been read."
_recent_creates = defaultdict(deque)
_rate_lock = threading.Lock()


def rate_limited(ip):
    now = time.time()
    with _rate_lock:
        q = _recent_creates[ip]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= CREATES_PER_MINUTE:
            return True
        q.append(now)
        return False


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ API

@app.get("/api/health")
def health():
    db().execute("SELECT 1")
    return jsonify(ok=True, time=int(time.time()))


@app.post("/api/notes")
def create_note():
    if rate_limited(request.remote_addr or "?"):
        return err(429, "slow_down", "Too many notes from your address. Wait a minute and try again.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err(400, "bad_json", "Request body must be a JSON object.")

    ciphertext, iv = data.get("ciphertext"), data.get("iv")
    ttl, max_views = data.get("ttlSeconds"), data.get("maxViews")
    protected = bool(data.get("protected", False))

    if not isinstance(ciphertext, str) or not B64_RE.match(ciphertext):
        return err(400, "bad_ciphertext", "ciphertext must be a base64 string.")
    if len(ciphertext) > MAX_CIPHERTEXT_CHARS:
        return err(413, "too_large", "This note is too long. Shorten it and try again.")
    if not isinstance(iv, str) or not B64_RE.match(iv) or len(iv) > 24:
        return err(400, "bad_iv", "iv must be a short base64 string.")
    if ttl not in ALLOWED_TTLS:
        return err(400, "bad_ttl", f"ttlSeconds must be one of {sorted(ALLOWED_TTLS)}.")
    if not isinstance(max_views, int) or isinstance(max_views, bool) or not 1 <= max_views <= MAX_VIEWS_LIMIT:
        return err(400, "bad_max_views", f"maxViews must be a whole number from 1 to {MAX_VIEWS_LIMIT}.")

    now = int(time.time())
    delete_token = secrets.token_urlsafe(24)
    for _ in range(3):  # retry on the astronomically unlikely ID collision
        note_id = secrets.token_urlsafe(9)  # 12 URL-safe characters, 72 random bits
        try:
            db().execute(
                "INSERT INTO notes (id, ciphertext, iv, protected, max_views, created_at, expires_at,"
                " delete_token_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (note_id, ciphertext, iv, int(protected), max_views, now, now + ttl, hash_token(delete_token)),
            )
            break
        except sqlite3.IntegrityError:
            continue
    else:
        return err(500, "id_collision", "Couldn't create the note. Try again.")

    log("note_created", ttl=ttl, maxViews=max_views, protected=protected)
    return jsonify(id=note_id, deleteToken=delete_token, expiresAt=now + ttl, maxViews=max_views), 201


@app.get("/api/notes/<note_id>")
def note_meta(note_id):
    now = int(time.time())
    row = None
    if ID_RE.match(note_id):
        row = db().execute(
            "SELECT max_views, view_count, created_at, expires_at, protected FROM notes"
            " WHERE id = ? AND view_count < max_views AND expires_at > ?",
            (note_id, now),
        ).fetchone()
    if row is None:
        return err(404, "gone", GONE)
    return jsonify(
        expiresAt=row["expires_at"], createdAt=row["created_at"], maxViews=row["max_views"],
        viewsLeft=row["max_views"] - row["view_count"], protected=bool(row["protected"]), serverTime=now,
    )


@app.post("/api/notes/<note_id>/open")
def open_note(note_id):
    if not ID_RE.match(note_id):
        return err(410, "gone", GONE)
    now = int(time.time())
    # One atomic statement: two people opening the last view at the same moment can't both succeed.
    row = db().execute(
        "UPDATE notes SET view_count = view_count + 1"
        " WHERE id = ? AND view_count < max_views AND expires_at > ?"
        " RETURNING ciphertext, iv, protected, max_views, view_count, expires_at",
        (note_id, now),
    ).fetchone()
    if row is None:
        return err(410, "gone", GONE)

    views_left = row["max_views"] - row["view_count"]
    burned = views_left <= 0
    if burned:  # last view: delete now instead of waiting for the cleanup job
        db().execute("DELETE FROM notes WHERE id = ?", (note_id,))
    log("note_opened", viewsLeft=views_left, burned=burned)
    return jsonify(
        ciphertext=row["ciphertext"], iv=row["iv"], protected=bool(row["protected"]),
        viewsLeft=views_left, burned=burned, expiresAt=row["expires_at"],
    )


@app.delete("/api/notes/<note_id>")
def delete_note(note_id):
    token = request.headers.get("x-delete-token", "")
    if not token:
        return err(401, "no_token", "The x-delete-token header is required.")
    deleted = 0
    if ID_RE.match(note_id):
        deleted = db().execute(
            "DELETE FROM notes WHERE id = ? AND delete_token_hash = ?", (note_id, hash_token(token))
        ).rowcount
    if not deleted:  # same answer for "missing" and "wrong token": reveals nothing
        return err(404, "gone", "Nothing to destroy: the note is gone or the token is wrong.")
    log("note_destroyed")
    return "", 204


@app.errorhandler(413)
def _too_large(_e):
    return err(413, "too_large", "Request is too large.")


@app.errorhandler(405)
def _not_allowed(_e):
    return err(405, "method_not_allowed", "That method isn't supported here.")


# ------------------------------------------------------------------ pages and static files

TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}


def _load_static():
    files = {}
    for root, _, names in os.walk(STATIC_DIR):
        for name in names:
            full = os.path.join(root, name)
            with open(full, "rb") as f:
                files[os.path.relpath(full, STATIC_DIR).replace(os.sep, "/")] = f.read()
    version = hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:10]
    return files, version


STATIC, VERSION = _load_static()
PAGES = {}
for _name in ("index", "view", "about", "404"):
    with open(os.path.join(PAGES_DIR, f"{_name}.html"), encoding="utf-8") as _f:
        PAGES[_name] = _f.read().replace("{{v}}", VERSION)  # ?v=<hash> busts browser caches on each deploy


def page(name, status=200):
    return Response(PAGES[name], status=status, mimetype="text/html",
                    headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return page("index")


@app.get("/n/<note_id>")
def view(note_id):
    return page("view")


@app.get("/about")
def about():
    return page("about")


@app.get("/static/<path:rel>")
def static_file(rel):
    body = STATIC.get(rel)
    if body is None:
        return page("404", 404)
    ext = os.path.splitext(rel)[1]
    return Response(body, mimetype=TYPES.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/favicon.ico")
def favicon():
    return static_file("favicon.svg")


@app.errorhandler(404)
def _not_found(_e):
    if request.path.startswith("/api/"):
        return err(404, "not_found", "No such endpoint.")
    return page("404", 404)


CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ startup

init_db()
threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True).start()

if __name__ == "__main__":  # local development without Docker: python app/main.py
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=False)
