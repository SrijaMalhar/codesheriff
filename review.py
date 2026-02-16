import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

GH = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK = os.getenv("GEMINI_FALLBACK", "gemini-2.5-flash-lite")
_GEMINI = (
    "https://generativelanguage.googleapis.com"
    "/v1beta/models/{}:generateContent?key={}"
)


def gh(method, path, **kwargs):
    accept = kwargs.pop("accept", "application/vnd.github+json")
    return httpx.request(
        method, f"{GH}{path}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": accept},
        timeout=30, **kwargs,
    )


def gemini_call(prompt):
    # try primary model, fall back to lite on 503
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for model in (GEMINI_MODEL, GEMINI_FALLBACK):
        url = _GEMINI.format(model, GOOGLE_API_KEY)
        r = httpx.post(url, json=payload, timeout=30)
        data = r.json()
        if "candidates" in data:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if data.get("error", {}).get("code") != 503:
            break
    raise RuntimeError(data.get("error", {}).get("message", "Gemini error"))


def verify_sig(body, header):
    if not WEBHOOK_SECRET:
        return True
    sig = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return sig == (header or "")


def _severity(code):
    return "warning" if code.startswith("W") else "info" if code.startswith("C") else "error"


def lint(filename, source):
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        tmp = f.name
    findings = []
    try:
        out = subprocess.run(
            ["flake8", "--max-line-length=120", "--format=%(row)d|%(col)d|%(code)s|%(text)s", tmp],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for line in out.splitlines():
            row, col, code, msg = line.split("|", 3)
            findings.append({
                "file": filename, "line": int(row), "col": int(col),
                "code": code, "msg": msg.strip(),
                "severity": _severity(code), "source": "flake8",
            })
    except Exception:
        pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return findings


def gemini_review(filename, source):
    # ask Gemini to spot bugs and security issues in the file
    if not GOOGLE_API_KEY:
        return []
    prompt = (
        f"Review this Python file for bugs, security issues, and bad practices.\n"
        f"File: {filename}\n```python\n{source[:4000]}\n```\n"
        'Return ONLY a JSON array. Each item: {"line":int,"severity":"error"|"warning"|"info",'
        '"msg":"concise"}. Return [] if nothing found. No markdown.'
    )
    try:
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", gemini_call(prompt)).strip()
        return [
            {"file": filename, "code": "AI", "col": 0, "source": "gemini", **f}
            for f in json.loads(raw) if isinstance(f, dict)
        ]
    except Exception:
        return []


def parse_diff_positions(diff_text):
    # maps filename -> {source_line: diff_position} for posting inline comments
    positions = {}
    cur, diff_pos, src_line = None, 0, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            positions[cur] = {}
            diff_pos = src_line = 0
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)", line)
            src_line = (int(m.group(1)) - 1) if m else src_line
            diff_pos += 1
        elif cur:
            diff_pos += 1
            if line.startswith("+"):
                src_line += 1
                positions[cur][src_line] = diff_pos
            elif not line.startswith("-"):
                src_line += 1
    return positions


_BADGE = {"error": "🔴", "warning": "🟡", "info": "🔵"}


def process_pr(owner, repo, pr_num, sha, shadow=False):
    diff_text = gh("GET", f"/repos/{owner}/{repo}/pulls/{pr_num}",
                   accept="application/vnd.github.v3.diff").text
    positions = parse_diff_positions(diff_text)

    changed = gh("GET", f"/repos/{owner}/{repo}/pulls/{pr_num}/files").json()
    sources = {}
    for f in changed:
        if not f["filename"].endswith(".py") or f.get("status") == "removed":
            continue
        try:
            raw = gh("GET", f"/repos/{owner}/{repo}/contents/{f['filename']}",
                     params={"ref": sha}).json()
            sources[f["filename"]] = base64.b64decode(raw["content"]).decode()
        except Exception:
            pass

    all_findings = []
    for filename, source in sources.items():
        all_findings += lint(filename, source) + gemini_review(filename, source)

    if shadow:
        return {"shadow": True, "findings": all_findings, "files": list(sources)}

    inline_comments, orphan = [], []
    for f in all_findings:
        pos = positions.get(f["file"], {}).get(f["line"])
        tag = f"{_BADGE.get(f['severity'], '')} **{f['severity'].upper()}**"
        body = f"{tag} `{f['code']}` — {f['msg']}"
        if f["source"] == "gemini":
            body += " *(Gemini)*"
        if pos:
            inline_comments.append({"path": f["file"], "position": pos, "body": body})
        else:
            orphan.append(f)

    errors = sum(1 for f in all_findings if f["severity"] == "error")
    warnings = sum(1 for f in all_findings if f["severity"] == "warning")
    summary = [
        "## 🤠 CodeSheriff Review", "",
        f"Reviewed **{len(sources)} file(s)** — "
        f"🔴 {errors} errors · 🟡 {warnings} warnings · "
        f"{len(inline_comments)} inline · {len(orphan)} outside diff.",
    ]
    if orphan:
        summary += ["", "**Issues outside the diff:**"]
        for i in orphan[:8]:
            summary.append(
                f"- {_BADGE.get(i['severity'],'')} `{i['file']}:{i['line']}` {i['msg']}"
            )
    summary += ["", "<sub>👍 useful · 👎 not useful — `POST /feedback`</sub>"]

    gh("POST", f"/repos/{owner}/{repo}/pulls/{pr_num}/reviews", json={
        "commit_id": sha, "body": "\n".join(summary),
        "event": "COMMENT", "comments": inline_comments,
    })
    return {"issues": len(all_findings), "inline": len(inline_comments),
            "errors": errors, "warnings": warnings, "files": list(sources)}
