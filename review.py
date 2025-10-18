import base64
import hashlib
import hmac
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

GH = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


def gh(method: str, path: str, **kwargs) -> httpx.Response:
    accept = kwargs.pop("accept", "application/vnd.github+json")
    return httpx.request(
        method, f"{GH}{path}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": accept},
        timeout=30, **kwargs,
    )


def verify_sig(body: bytes, header: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    sig = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, header or "")


def lint(filename: str, source: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        tmp = f.name
    issues: list[dict] = []
    try:
        out = subprocess.run(
            ["flake8", "--max-line-length=120", "--format=%(row)d|%(col)d|%(code)s|%(text)s", tmp],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for line in out.splitlines():
            row, col, code, msg = line.split("|", 3)
            issues.append({"file": filename, "line": int(row), "col": int(col),
                           "code": code, "msg": msg.strip()})
    except Exception:
        pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return issues


def parse_diff_positions(diff_text: str) -> dict[str, dict[int, int]]:
    """Map (filename, source_line) -> diff_position for inline PR comments."""
    positions: dict[str, dict[int, int]] = {}
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


def process_pr(owner: str, repo: str, pr_num: int, sha: str, shadow: bool = False) -> dict:
    # Fetch unified diff for position mapping
    diff_text = gh("GET", f"/repos/{owner}/{repo}/pulls/{pr_num}",
                   accept="application/vnd.github.v3.diff").text
    positions = parse_diff_positions(diff_text)

    # Fetch all changed Python files (multi-file context)
    changed = gh("GET", f"/repos/{owner}/{repo}/pulls/{pr_num}/files").json()
    sources: dict[str, str] = {}
    for f in changed:
        if not f["filename"].endswith(".py") or f.get("status") == "removed":
            continue
        try:
            raw = gh("GET", f"/repos/{owner}/{repo}/contents/{f['filename']}",
                     params={"ref": sha}).json()
            sources[f["filename"]] = base64.b64decode(raw["content"]).decode()
        except Exception:
            pass

    # Lint every file; gather cross-file issue list (context-aware summary)
    all_issues: list[dict] = []
    for filename, source in sources.items():
        all_issues.extend(lint(filename, source))

    if shadow:
        # Shadow mode: analyse but never post — return results for logging
        return {"shadow": True, "issues": all_issues, "files": list(sources)}

    # Split into inline (has diff position) and orphan (outside the diff)
    inline_comments, orphan = [], []
    for issue in all_issues:
        pos = positions.get(issue["file"], {}).get(issue["line"])
        if pos:
            inline_comments.append({
                "path": issue["file"], "position": pos,
                "body": (f"`{issue['code']}` — {issue['msg']}\n"
                         f"*(line {issue['line']}, col {issue['col']})*"),
            })
        else:
            orphan.append(issue)

    # Build context-aware review summary
    body = [
        "## 🤠 CodeSheriff Review",
        "",
        f"Reviewed **{len(sources)} file(s)**, "
        f"found **{len(all_issues)} issue(s)** "
        f"({len(inline_comments)} inline, {len(orphan)} outside diff).",
    ]
    if orphan:
        body += ["", "**Issues outside the diff:**"]
        for i in orphan[:10]:
            body.append(f"- `{i['file']}:{i['line']}` `{i['code']}` {i['msg']}")
    body += [
        "",
        "<sub>👍 useful · 👎 not useful — "
        "send feedback via `POST /feedback`</sub>",
    ]

    gh("POST", f"/repos/{owner}/{repo}/pulls/{pr_num}/reviews", json={
        "commit_id": sha,
        "body": "\n".join(body),
        "event": "COMMENT",
        "comments": inline_comments,
    })

    return {"issues": len(all_issues), "inline": len(inline_comments), "files": list(sources)}
