"""Tests for atomic credit debit and ad-play concurrency."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("NERVIX_SESSION_SECRET", "test-secret-for-ci")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")

from fastapi.testclient import TestClient
from nervix_platform.main import app, db
from nervix_platform.models import AdPlayEvent

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    empty = {"members": {}, "projects": {}, "transactions": [], "ad_plays": []}
    db.path.parent.mkdir(parents=True, exist_ok=True)
    with open(db.path, "w") as f:
        json.dump(empty, f)
    client.cookies.clear()
    yield


def test_ad_play_deducts_credit():
    # Seed member with 5 credits
    from nervix_platform.models import Member, Project
    member = Member(email="a@b.com", name="A", credits=5)
    db.create_member(member.model_dump())
    project = Project(member_id=member.id, title="P", description="D")
    db.create_project(project.model_dump())

    event = AdPlayEvent(project_id=project.id, session_id="s1", completed=True)
    r = client.post("/api/ad-play", json=event.model_dump(mode="json"))
    assert r.status_code == 200
    assert r.json()["credits_deducted"] == 1

    m = db.get_member(member.id)
    assert m["credits"] == 4


def test_ad_play_insufficient_credits():
    from nervix_platform.models import Member, Project
    member = Member(email="a@b.com", name="A", credits=0)
    db.create_member(member.model_dump())
    project = Project(member_id=member.id, title="P", description="D")
    db.create_project(project.model_dump())

    event = AdPlayEvent(project_id=project.id, session_id="s1", completed=True)
    r = client.post("/api/ad-play", json=event.model_dump(mode="json"))
    assert r.status_code == 402


def test_atomic_debit_concurrency():
    """20 concurrent plays on a 5-credit balance must result in exactly 5 successes."""
    from nervix_platform.models import Member, Project
    member = Member(email="a@b.com", name="A", credits=5)
    db.create_member(member.model_dump())
    project = Project(member_id=member.id, title="P", description="D")
    db.create_project(project.model_dump())

    successes = []
    errors = []

    def _play():
        event = AdPlayEvent(project_id=project.id, session_id="s1", completed=True)
        r = client.post("/api/ad-play", json=event.model_dump(mode="json"))
        if r.status_code == 200:
            successes.append(1)
        else:
            errors.append(r.status_code)

    threads = [threading.Thread(target=_play) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 5
    assert len(errors) == 15
    m = db.get_member(member.id)
    assert m["credits"] == 0


def test_partial_play_no_charge():
    """Partial plays cost 0 until fractional credit schema is implemented."""
    from nervix_platform.models import Member, Project
    member = Member(email="a@b.com", name="A", credits=5)
    db.create_member(member.model_dump())
    project = Project(member_id=member.id, title="P", description="D")
    db.create_project(project.model_dump())

    event = AdPlayEvent(project_id=project.id, session_id="s1", completed=False)
    r = client.post("/api/ad-play", json=event.model_dump(mode="json"))
    assert r.status_code == 200
    assert r.json()["credits_deducted"] == 0
    m = db.get_member(member.id)
    assert m["credits"] == 5
