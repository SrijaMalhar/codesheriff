import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import analysis_service.main as an_main

client = TestClient(an_main.app)

ANALYZE_PAYLOAD = {
    "pr_number": 7,
    "repo": "testuser/testrepo",
    "owner": "testuser",
    "head_sha": "deadbeef1234",
    "shadow_mode": True,
    "review_log_id": 0,
}

MOCK_DIFF = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,5 @@
+import os
+x=1
 def hello():
     pass
"""

MOCK_FILES = [
    {"filename": "foo.py", "status": "modified"},
]

MOCK_GEMINI_RESPONSE = {
    "issues": [
        {"file": "foo.py", "line": 2, "severity": "warning", "suggestion": "Missing whitespace around operator"},
    ],
    "summary": "Minor style issues found.",
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_run_flake8_finds_error():
    from analysis_service.main import run_flake8
    bad_code = "import os\nx=1\n"
    findings = run_flake8("test_file.py", bad_code)
    assert len(findings) > 0
    codes = [f.suggestion for f in findings]
    assert any("E" in c or "W" in c for c in codes)


def test_run_flake8_clean_code():
    from analysis_service.main import run_flake8
    clean_code = "x = 1\n"
    findings = run_flake8("clean.py", clean_code)
    assert findings == []


def test_analyze_endpoint_shadow_mode():
    with patch("analysis_service.main.fetch_pr_diff", return_value=MOCK_DIFF), \
         patch("analysis_service.main.fetch_pr_files", return_value=MOCK_FILES), \
         patch("analysis_service.main.fetch_file_content", return_value="import os\nx=1\n"), \
         patch("analysis_service.main.run_gemini_review", return_value=([], "No AI issues.")), \
         patch("analysis_service.main.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = MagicMock(return_value=MagicMock(
            post=MagicMock(return_value=MagicMock(raise_for_status=MagicMock()))
        ))
        mock_client.return_value.__aexit__ = MagicMock(return_value=False)
        resp = client.post("/analyze", json=ANALYZE_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["pr_number"] == 7
    assert data["findings_count"] >= 0


def test_gemini_skipped_without_api_key():
    import analysis_service.main as m
    original = m.GOOGLE_API_KEY
    m.GOOGLE_API_KEY = ""
    findings, summary = m.run_gemini_review("some diff")
    m.GOOGLE_API_KEY = original
    assert findings == []
    assert "skipped" in summary.lower()


def test_gemini_handles_bad_json():
    import analysis_service.main as m
    mock_model = MagicMock()
    mock_model.generate_content.return_value.text = "not valid json"
    with patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.configure"):
        findings, summary = m.run_gemini_review("diff content")
    assert findings == []
