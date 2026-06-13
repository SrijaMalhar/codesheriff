from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import comment_service.main as cm_main

cm_main.GITHUB_TOKEN = "ghp_test000000000000000000000000000000"

client = TestClient(cm_main.app)

BASE_REVIEW = {
    "pr_number": 5,
    "repo": "testuser/testrepo",
    "owner": "testuser",
    "head_sha": "cafebabe",
    "shadow_mode": True,
    "findings": [],
    "summary": "All good.",
}

FINDING = {
    "file": "main.py",
    "line": 10,
    "severity": "warning",
    "suggestion": "Line too long",
    "source": "flake8",
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_shadow_mode_no_github_calls():
    payload = {"review": BASE_REVIEW, "review_log_id": 1}
    with patch("httpx.post") as mock_post:
        resp = client.post("/comment", json=payload)
        mock_post.assert_not_called()
    assert resp.status_code == 200
    assert resp.json()["status"] == "shadow"


def test_shadow_mode_with_findings():
    review = {**BASE_REVIEW, "findings": [FINDING]}
    payload = {"review": review, "review_log_id": 1}
    with patch("httpx.post") as mock_post:
        resp = client.post("/comment", json=payload)
        mock_post.assert_not_called()
    assert resp.status_code == 200
    assert resp.json()["findings_count"] == 1


def test_live_mode_posts_comments():
    review = {**BASE_REVIEW, "shadow_mode": False, "findings": [FINDING]}
    payload = {"review": review, "review_log_id": 1}

    mock_resp_comment = MagicMock()
    mock_resp_comment.status_code = 201

    mock_resp_summary = MagicMock()
    mock_resp_summary.status_code = 200

    with patch("httpx.post", side_effect=[mock_resp_comment, mock_resp_summary]) as mock_post:
        resp = client.post("/comment", json=payload)
        assert mock_post.call_count == 2

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["comments_posted"] == 1
    assert data["comments_failed"] == 0


def test_live_mode_handles_github_error():
    review = {**BASE_REVIEW, "shadow_mode": False, "findings": [FINDING]}
    payload = {"review": review, "review_log_id": 1}

    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 422

    mock_resp_summary = MagicMock()
    mock_resp_summary.status_code = 200

    with patch("httpx.post", side_effect=[mock_resp_fail, mock_resp_summary]):
        resp = client.post("/comment", json=payload)

    data = resp.json()
    assert data["comments_posted"] == 0
    assert data["comments_failed"] == 1


def test_live_mode_no_token():
    original = cm_main.GITHUB_TOKEN
    cm_main.GITHUB_TOKEN = ""
    review = {**BASE_REVIEW, "shadow_mode": False}
    payload = {"review": review, "review_log_id": 1}
    resp = client.post("/comment", json=payload)
    cm_main.GITHUB_TOKEN = original
    assert resp.status_code == 503
