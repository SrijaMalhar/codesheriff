# Contributing

Thanks for your interest. Here's everything you need to get started.

## Local setup

```bash
git clone https://github.com/SrijaMalhar/codesheriff.git
cd codesheriff
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_TOKEN, WEBHOOK_SECRET, GOOGLE_API_KEY
bash run_all.sh
```

The four services start on ports 8001–8004. The dashboard is at http://localhost:8004.

## Running the linter

```bash
flake8 . --max-line-length=120 --extend-ignore=E501
```

CI runs this automatically on every push and PR.

## Project layout

```
analysis_service/   FastAPI — fetches diffs, runs flake8 + Gemini review
webhook_service/    FastAPI — receives and verifies GitHub webhook events
comment_service/    FastAPI — posts inline review comments back to GitHub
dashboard/          Flask + Chart.js — review history and stats
shared/             Models, DB schema, config (imported by all services)
```

## Making changes

- One logical change per PR.
- Keep lines under 120 characters.
- No commented-out code or debug prints in PRs.
- If you change the DB schema, update `shared/database.py` and note the migration in the PR description.

## Environment variables

See the table in [README.md](README.md#environment-variables) for the full list.
Use `SHADOW_MODE=true` while testing so reviews are logged but nothing is posted to GitHub.

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Relevant log output from `logs/`
