# Demo

## What it looks like on a real PR

When CodeSheriff runs on a pull request, it posts a top-level review comment
with a summary, and inline comments directly on the diff lines with issues.

---

### Summary comment

> ## 🤠 CodeSheriff Review
>
> Reviewed **2 file(s)** — 🔴 1 error · 🟡 3 warnings · 4 inline · 1 outside diff.
>
> **Issues outside the diff:**
> - 🟡 `utils/helpers.py:12` local variable 'result' is assigned but never used
>
> <sub>👍 useful · 👎 not useful — `POST /feedback`</sub>

---

### Inline comment (on the diff)

```
app/routes.py  line 34
────────────────────────────────────────────
+ def get_user(id):
+     user = db.query(f"SELECT * FROM users WHERE id={id}")
+     return user
```

> 🔴 **ERROR** `AI` — SQL query built with f-string, vulnerable to injection.
> Use parameterised queries instead. *(Gemini)*

---

### Dashboard (`/dashboard`)

The web dashboard lists all reviewed PRs with issue counts and timestamps:

| PR | Repo | Issues | Inline | Mode | When |
|---|---|---|---|---|---|
| #12 | SrijaMalhar/myproject | 4 | 3 | live | 2026-02-14 11:32 |
| #11 | SrijaMalhar/myproject | 0 | 0 | live | 2026-02-13 18:05 |
| #9  | SrijaMalhar/myproject | 7 | 6 | shadow | 2026-02-10 14:22 |

---

## Using the feedback loop

After CodeSheriff posts a comment, you can tell it whether the comment was useful:

```bash
curl -X POST https://<your-host>/feedback \
  -H "Content-Type: application/json" \
  -d '{"comment_id": "1234567890", "vote": "down", "pr_id": "12"}'
```

Thumbs-down feedback accumulates in SQLite. Running `POST /feedback/retrain`
sends those comments to Gemini and asks it to suggest improvements — the
suggestions are stored and viewable at `GET /suggestions`.

---

## Running it yourself

The quickest way to try it without setting up a webhook is to run
`sheriff.py` directly against any open PR in a repo you have access to:

```bash
export GITHUB_TOKEN=your_pat
export GOOGLE_API_KEY=your_gemini_key
export REPO_OWNER=SrijaMalhar
export REPO_NAME=myproject
export PR_NUMBER=12
export HEAD_SHA=abc1234

python sheriff.py
```
