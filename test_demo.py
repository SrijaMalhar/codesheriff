#!/usr/bin/env python3
"""
CodeSheriff — End-to-End Demo Script
======================================
Simulates a GitHub pull_request webhook event and drives the full pipeline:

    webhook-service (8001)
        → analysis-service (8002)  [flake8 + Gemini AI]
            → comment-service (8003)
                → dashboard (8004)

Usage examples
--------------
  # Full end-to-end test against a real public GitHub PR:
  python test_demo.py --owner pallets --repo flask --pr 5609

  # Dry-run: tests only webhook reception + DB logging (no GitHub API calls):
  python test_demo.py --dry-run

  # Override just the PR number (uses the default owner/repo):
  python test_demo.py --pr 5700

Prerequisites
-------------
  - All four services must be running  (see run_all.sh)
  - GITHUB_TOKEN must be set in .env or the environment
  - GOOGLE_API_KEY is optional; AI review is gracefully skipped when absent
  - WEBHOOK_SECRET is optional; signature verification is skipped when absent
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: make shared importable when run from the codesheriff/ directory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from shared.database import ReviewLog, SessionLocal, create_tables  # noqa: E402

# ---------------------------------------------------------------------------
# Service endpoints
# ---------------------------------------------------------------------------
WEBHOOK_URL   = "http://localhost:8001"
ANALYSIS_URL  = "http://localhost:8002"
COMMENT_URL   = "http://localhost:8003"
DASHBOARD_URL = "http://localhost:8004"

# ---------------------------------------------------------------------------
# Default public Python repo used when the user does not supply --owner/--repo
# (This is the Flask project on GitHub; it has many open PRs.)
# ---------------------------------------------------------------------------
DEFAULT_OWNER   = "pallets"
DEFAULT_REPO    = "flask"
DEFAULT_PR      = 5609           # a known Flask PR; change freely
DEFAULT_SHA     = "deadbeef" * 5  # placeholder head SHA for dry-run


# ===========================================================================
# Helpers
# ===========================================================================

def _color(code: str, text: str) -> str:
    """Wrap text in ANSI colour codes (stripped on Windows if unsupported)."""
    return f"\033[{code}m{text}\033[0m"

def green(t):  return _color("32", t)
def yellow(t): return _color("33", t)
def red(t):    return _color("31", t)
def bold(t):   return _color("1",  t)
def dim(t):    return _color("2",  t)


def _sign(payload: bytes) -> str:
    """Compute the X-Hub-Signature-256 header value, or empty string if
    WEBHOOK_SECRET is not configured."""
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        return ""
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ===========================================================================
# Step 1 – Health checks
# ===========================================================================

def check_health() -> bool:
    """Ping the /health endpoint of each service.  Returns False if any are down."""
    services = {
        "webhook-service":  f"{WEBHOOK_URL}/health",
        "analysis-service": f"{ANALYSIS_URL}/health",
        "comment-service":  f"{COMMENT_URL}/health",
        "dashboard":        f"{DASHBOARD_URL}/",
    }

    print(bold("\n── Step 1: Service health checks ──────────────────────────────"))
    all_ok = True

    for name, url in services.items():
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code < 400:
                print(f"  {green('✓')} {name:25s} {dim(url)}")
            else:
                print(f"  {red('✗')} {name:25s} HTTP {r.status_code}")
                all_ok = False
        except httpx.ConnectError:
            print(f"  {red('✗')} {name:25s} {red('connection refused — is it running?')}")
            all_ok = False

    if not all_ok:
        print(red("\n  One or more services are down.  Run ./run_all.sh and retry.\n"))
    return all_ok


# ===========================================================================
# Step 2 – Fire a simulated webhook payload
# ===========================================================================

def build_payload(owner: str, repo: str, pr_number: int, head_sha: str,
                  action: str = "opened") -> dict:
    """Build a minimal but realistic GitHub pull_request webhook payload."""
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "number": pr_number,
            "title": f"[DEMO] Test PR #{pr_number}",
            "state": "open",
            "head": {"sha": head_sha, "ref": "feature/test"},
            "base": {"ref": "main"},
            "user": {"login": "demo-author"},
            "body": "Automated demo payload generated by test_demo.py",
        },
        "repository": {
            "full_name": f"{owner}/{repo}",
            "name": repo,
            "private": False,
            "owner": {"login": owner},
        },
        "sender": {"login": "demo-author"},
    }


def send_webhook(owner: str, repo: str, pr_number: int, head_sha: str) -> int | None:
    """POST the simulated webhook to webhook-service and return the review_log_id."""
    payload_dict = build_payload(owner, repo, pr_number, head_sha)
    raw_body     = json.dumps(payload_dict, separators=(",", ":")).encode()
    sig          = _sign(raw_body)

    headers = {
        "Content-Type":          "application/json",
        "X-GitHub-Event":        "pull_request",
        "X-Hub-Signature-256":   sig or "sha256=0" * 64,
        "X-GitHub-Delivery":     "demo-delivery-001",
    }

    print(bold("\n── Step 2: Sending webhook payload ────────────────────────────"))
    print(f"  Target  : {WEBHOOK_URL}/webhook")
    print(f"  Event   : pull_request / opened")
    print(f"  PR      : {owner}/{repo} #{pr_number}")
    print(f"  Signed  : {'yes (' + sig[:20] + '…)' if sig else yellow('no (WEBHOOK_SECRET not set)')}")

    try:
        r = httpx.post(f"{WEBHOOK_URL}/webhook", content=raw_body, headers=headers, timeout=15)
    except httpx.ConnectError:
        print(red("  ✗  Cannot reach webhook-service on port 8001"))
        return None

    print(f"  Status  : HTTP {r.status_code}")
    try:
        resp = r.json()
        print(f"  Response: {json.dumps(resp, indent=4)}")
    except Exception:
        print(f"  Body    : {r.text[:300]}")
        return None

    if r.status_code != 200:
        print(red("  ✗  Webhook was rejected"))
        return None

    log_id = resp.get("review_log_id")
    print(green(f"  ✓  ReviewLog created — id={log_id}"))
    return log_id


# ===========================================================================
# Step 3 – Poll until analysis completes (or times out)
# ===========================================================================

def poll_review(log_id: int, timeout: int = 90) -> dict | None:
    """Query the DB until the ReviewLog row reaches a terminal status."""
    print(bold("\n── Step 3: Waiting for analysis to complete ───────────────────"))

    deadline = time.time() + timeout
    last_status = None

    while time.time() < deadline:
        db: Session = SessionLocal()
        try:
            log = db.get(ReviewLog, log_id)
            if log is None:
                print(red(f"  ✗  ReviewLog id={log_id} not found in DB"))
                return None

            status = log.status
            if status != last_status:
                icon = {"pending": "…", "completed": "✓", "failed": "✗"}.get(status, "?")
                colour = {"completed": green, "failed": red}.get(status, yellow)
                print(f"  {colour(icon)}  status = {colour(status)}")
                last_status = status

            if status in ("completed", "failed"):
                findings = log.get_findings() if status == "completed" else []
                return {
                    "id":        log.id,
                    "repo":      log.repo,
                    "pr_number": log.pr_id,
                    "status":    log.status,
                    "mode":      log.mode,
                    "findings":  findings,
                    "created_at": str(log.created_at),
                }
        finally:
            db.close()

        time.sleep(3)

    print(yellow(f"  ⏱  Timed out after {timeout}s — analysis may still be running"))
    return None


# ===========================================================================
# Step 4 – Print results
# ===========================================================================

def print_results(result: dict) -> None:
    """Display a formatted summary of the completed review."""
    print(bold("\n── Step 4: Review results ─────────────────────────────────────"))

    status_str = green("completed") if result["status"] == "completed" else red(result["status"])
    print(f"  Review ID : {result['id']}")
    print(f"  Repo      : {result['repo']}  PR #{result['pr_number']}")
    print(f"  Status    : {status_str}")
    print(f"  Mode      : {result['mode']}  (shadow = comments not posted to GitHub)")
    print(f"  Created   : {result['created_at']}")

    findings = result.get("findings", [])
    if not findings:
        print(f"\n  {dim('No findings recorded (analysis may have failed or PR was empty)')}")
        return

    print(f"\n  Findings ({len(findings)} total):")
    for i, f in enumerate(findings[:10], 1):   # show at most 10 findings
        sev     = f.get("severity", "info")
        colour  = {"error": red, "warning": yellow}.get(sev, dim)
        source  = dim(f"[{f.get('source', '?')}]")
        loc     = f"{f.get('file', '?')}:{f.get('line', '?')}"
        msg     = f.get("suggestion", "")[:90]
        print(f"  {i:2d}. {colour(sev.upper():7s)} {source} {loc}")
        print(f"      {msg}")

    if len(findings) > 10:
        print(dim(f"\n  … and {len(findings) - 10} more findings. See the dashboard for the full list."))


# ===========================================================================
# Dry-run mode: test only webhook reception + DB logging
# ===========================================================================

def dry_run(owner: str, repo: str, pr_number: int) -> None:
    """Sends a signed webhook with a fake SHA.  The analysis-service will
    attempt to call GitHub and fail — this is expected in dry-run mode.
    The goal is to verify signature checking, DB logging, and error handling."""

    print(yellow(bold("\n  DRY-RUN MODE")))
    print(dim("  Analysis will fail (fake SHA / possibly fake repo) — that is expected."))
    print(dim("  This mode validates: webhook reception, HMAC signing, and DB error logging.\n"))

    if not check_health():
        return

    log_id = send_webhook(owner, repo, pr_number, DEFAULT_SHA)
    if log_id is None:
        return

    # Wait briefly for the background task to reach the analysis service
    print(bold("\n── Step 3: Waiting for analysis attempt ───────────────────────"))
    print(dim("  (Expecting 'failed' status — GitHub API will reject the fake SHA)"))
    result = poll_review(log_id, timeout=30)

    if result:
        print_results(result)
    else:
        print(yellow("  Review row not found or still pending."))

    print(bold("\n── Done (dry-run) ─────────────────────────────────────────────"))
    print(f"  Dashboard : {DASHBOARD_URL}")


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeSheriff end-to-end demo script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--owner",   default=DEFAULT_OWNER,
                        help=f"GitHub repo owner  (default: {DEFAULT_OWNER})")
    parser.add_argument("--repo",    default=DEFAULT_REPO,
                        help=f"GitHub repo name   (default: {DEFAULT_REPO})")
    parser.add_argument("--pr",      type=int, default=DEFAULT_PR,
                        help=f"Pull request number (default: {DEFAULT_PR})")
    parser.add_argument("--timeout", type=int, default=90,
                        help="Seconds to wait for analysis to complete (default: 90)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test webhook + DB only — no real GitHub API calls needed")
    args = parser.parse_args()

    print(bold("╔══════════════════════════════════════════════════════════════╗"))
    print(bold("║           CodeSheriff  —  End-to-End Demo                   ║"))
    print(bold("╚══════════════════════════════════════════════════════════════╝"))

    # Ensure DB tables exist before polling
    create_tables()

    if args.dry_run:
        dry_run(args.owner, args.repo, args.pr)
        return

    # ── Full run ────────────────────────────────────────────────────────────
    if not check_health():
        sys.exit(1)

    # Fetch the real HEAD SHA from GitHub so the analysis service can fetch the diff
    print(bold("\n── Fetching real PR head SHA from GitHub ───────────────────────"))
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print(yellow("  GITHUB_TOKEN not set — will use a placeholder SHA (analysis may fail)"))
        head_sha = DEFAULT_SHA
    else:
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{args.owner}/{args.repo}/pulls/{args.pr}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            r.raise_for_status()
            pr_data  = r.json()
            head_sha = pr_data["head"]["sha"]
            title    = pr_data.get("title", "")
            state    = pr_data.get("state", "")
            print(f"  {green('✓')} PR #{args.pr}: {title!r}  [{state}]")
            print(f"  HEAD SHA: {head_sha}")
        except httpx.HTTPError as exc:
            print(yellow(f"  Could not fetch PR from GitHub ({exc}) — using placeholder SHA"))
            head_sha = DEFAULT_SHA

    log_id = send_webhook(args.owner, args.repo, args.pr, head_sha)
    if log_id is None:
        sys.exit(1)

    result = poll_review(log_id, timeout=args.timeout)

    if result:
        print_results(result)
        print(bold("\n── Done ──────────────────────────────────────────────────────"))
        print(f"  {green('✓')} Review complete — open the dashboard to see the full report:")
        print(f"  {bold(DASHBOARD_URL)}")
    else:
        print(red("\n  Analysis did not complete within the timeout."))
        print(dim(f"  Check logs or visit the dashboard: {DASHBOARD_URL}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
