"""Smoke tests for NERVIX dashboard, auth, and project endpoints."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("NERVIX_SESSION_SECRET", "test-secret-for-ci")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

from fastapi.testclient import TestClient
from nervix_platform.main import app, db

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset the JSON-file DB before each test."""
    import json

    empty = {"members": {}, "projects": {}, "transactions": [], "ad_plays": []}
    db.path.parent.mkdir(parents=True, exist_ok=True)
    with open(db.path, "w") as f:
        json.dump(empty, f)
    yield


def test_create_member_mints_cookie():
    r = client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    assert r.status_code == 200
    assert "nervix_member" in r.cookies
    assert r.json()["member_id"]


def test_create_existing_member_reuses_cookie():
    client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    r = client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    assert r.status_code == 200
    assert r.json()["message"] == "Member already exists"
    assert "nervix_member" in r.cookies


def test_dashboard_requires_cookie():
    r = client.get("/dashboard")
    assert r.status_code == 401


def test_dashboard_with_cookie_ok():
    r = client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    member_id = r.json()["member_id"]
    r2 = client.get("/dashboard", cookies={"nervix_member": r.cookies["nervix_member"]})
    assert r2.status_code == 200


def test_projects_requires_cookie():
    r = client.post("/api/projects", data={"title": "t", "description": "d"})
    assert r.status_code == 401


def test_projects_ignores_smuggled_member_id():
    r = client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    cookie = r.cookies["nervix_member"]
    r2 = client.post(
        "/api/projects",
        data={"title": "t", "description": "d", "member_id": "99999999999999"},
        cookies={"nervix_member": cookie},
    )
    assert r2.status_code == 200
    # The project should be created for the cookie member, not the smuggled id
    projects = db.list_projects()
    assert len(projects) == 1
    assert projects[0]["member_id"] == r.json()["member_id"]


def test_checkout_requires_cookie():
    r = client.post("/api/checkout", json={
        "tier": "starter",
        "success_url": "http://example.com/success",
        "cancel_url": "http://example.com/cancel",
    })
    assert r.status_code == 401


def test_create_project_and_list():
    r = client.post("/api/members", data={"email": "dan@example.com", "name": "Dan"})
    cookie = r.cookies["nervix_member"]
    r2 = client.post(
        "/api/projects",
        data={"title": "Test Project", "description": "A test"},
        cookies={"nervix_member": cookie},
    )
    assert r2.status_code == 200
    assert r2.json()["project_id"]
