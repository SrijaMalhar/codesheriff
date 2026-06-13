import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import webhook_service.main as wh_main

wh_main.WEBHOOK_SECRET = "test-secret-abc123"
wh_main.SHADOW_MODE = True

client = TestClient(wh_main.app)

TEST_SECRET = "test-secret-abc123"


def sign(body: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "head": {"sha": "abc123def456"},
    },
    "repository": {
        "full_name": "testuser/testrepo",
        "owner": {"login": "testuser"},
    },
    "sender": {"login": "testuser"},
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_invalid_signature_rejected():
    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": "sha256=badhash", "x-github-event": "pull_request"},
    )
    assert resp.status_code == 401


def test_missing_signature_rejected():
    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"x-github-event": "pull_request"},
    )
    assert resp.status_code == 401


def test_non_pr_event_ignored():
    body = json.dumps({"action": "created"}).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": sign(body), "x-github-event": "push"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_pr_closed_ignored():
    payload = {**PR_PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": sign(body), "x-github-event": "pull_request"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_pr_opened_queued():
    body = json.dumps(PR_PAYLOAD).encode()
    with patch.object(wh_main, "dispatch_analysis", new=AsyncMock(return_value=None)):
        resp = client.post(
            "/webhook",
            content=body,
            headers={"x-hub-signature-256": sign(body), "x-github-event": "pull_request"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["pr_number"] == 42
    assert data["repo"] == "testuser/testrepo"


def test_pr_synchronize_queued():
    payload = {**PR_PAYLOAD, "action": "synchronize"}
    body = json.dumps(payload).encode()
    with patch.object(wh_main, "dispatch_analysis", new=AsyncMock(return_value=None)):
        resp = client.post(
            "/webhook",
            content=body,
            headers={"x-hub-signature-256": sign(body), "x-github-event": "pull_request"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_verify_signature_direct():
    from webhook_service.main import verify_signature
    payload = b"hello world"
    good_sig = sign(payload)
    assert verify_signature(payload, good_sig) is True
    assert verify_signature(payload, "sha256=bad") is False
    assert verify_signature(payload, "not-sha256") is False
