import importlib
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

NOTE = {"ciphertext": "QUJDREVGRw==", "iv": "AAAAAAAAAAAAAAAA", "ttlSeconds": 3600, "maxViews": 2}


@pytest.fixture
def main(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CREATES_PER_MINUTE", "1000")
    sys.modules.pop("main", None)
    mod = importlib.import_module("main")
    mod.app.config["TESTING"] = True
    return mod


@pytest.fixture
def client(main):
    return main.app.test_client()


def create(client, **overrides):
    r = client.post("/api/notes", json={**NOTE, **overrides})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_create_and_meta(client):
    note = create(client)
    assert len(note["id"]) == 12 and note["deleteToken"]
    meta = client.get(f"/api/notes/{note['id']}").get_json()
    assert meta["viewsLeft"] == 2 and "ciphertext" not in meta


def test_meta_does_not_use_a_view(client):
    note = create(client, maxViews=1)
    for _ in range(3):
        assert client.get(f"/api/notes/{note['id']}").get_json()["viewsLeft"] == 1


def test_views_count_down_then_burn(client, main):
    note = create(client, maxViews=2)
    first = client.post(f"/api/notes/{note['id']}/open").get_json()
    assert first["viewsLeft"] == 1 and not first["burned"] and first["ciphertext"] == NOTE["ciphertext"]
    second = client.post(f"/api/notes/{note['id']}/open").get_json()
    assert second["viewsLeft"] == 0 and second["burned"]
    # really gone from the database, not just hidden
    assert main.connect().execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    assert client.post(f"/api/notes/{note['id']}/open").status_code == 410
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


def test_concurrent_last_view_has_one_winner(main):
    note = create(main.app.test_client(), maxViews=1)
    results = []

    def attempt():
        results.append(main.app.test_client().post(f"/api/notes/{note['id']}/open").status_code)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(200) == 1 and results.count(410) == 7


def test_expired_note_is_gone_and_cleaned_up(client, main):
    note = create(client)
    conn = main.connect()
    conn.execute("UPDATE notes SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, note["id"]))
    assert client.get(f"/api/notes/{note['id']}").status_code == 404
    assert client.post(f"/api/notes/{note['id']}/open").status_code == 410
    assert main.delete_expired() == 1


def test_delete_needs_the_right_token(client):
    note = create(client)
    path = f"/api/notes/{note['id']}"
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers={"x-delete-token": "wrong"}).status_code == 404
    assert client.delete(path, headers={"x-delete-token": note["deleteToken"]}).status_code == 204
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("override", [
    {"ttlSeconds": 999}, {"maxViews": 0}, {"maxViews": 11}, {"maxViews": True},
    {"ciphertext": "not base64!!"}, {"ciphertext": "A" * 16004}, {"iv": "A" * 40},
])
def test_validation(client, override):
    assert client.post("/api/notes", json={**NOTE, **override}).status_code in (400, 413)


def test_rate_limit(main, monkeypatch):
    monkeypatch.setattr(main, "CREATES_PER_MINUTE", 3)
    c = main.app.test_client()
    codes = [c.post("/api/notes", json=NOTE).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]


def test_pages_and_assets(client, main):
    home = client.get("/")
    assert home.status_code == 200 and b"Share a secret" in home.data
    assert b"{{v}}" not in home.data and f"?v={main.VERSION}".encode() in home.data
    assert "default-src 'none'" in home.headers["Content-Security-Policy"]
    assert b"Someone sent you a secret" in client.get("/n/abcDEF123456").data
    css = client.get("/static/style.css")
    assert css.status_code == 200 and "immutable" in css.headers["Cache-Control"]
    assert client.get("/static/../main.py").status_code == 404
    assert client.get("/nope").status_code == 404
    assert client.get("/api/nope").get_json()["error"] == "not_found"
