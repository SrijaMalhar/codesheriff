# Changelog

All notable changes to this project will be documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [0.2.0] — 2025-06-13

### Changed
- Split monolithic dashboard template into separate HTML, CSS, and JS files
- Trimmed all service files — removed verbose comments and section banners
- Pinned all dependencies to latest stable versions in `requirements.txt`

### Added
- `.github/workflows/ci.yml` — flake8 lint runs on every push and PR
- `setup.cfg` — centralised flake8 configuration
- `CONTRIBUTING.md` — setup guide and PR guidelines
- `.env.example` — documents all required and optional environment variables
- `dashboard/static/` — extracted CSS and JS from the dashboard template

### Removed
- `test_demo.py` — replaced by the webhook setup wizard in the dashboard

---

## [0.1.0] — 2025-05-01

### Added
- `webhook_service` — receives and verifies GitHub webhook events (HMAC-SHA256)
- `analysis_service` — fetches PR diffs, runs flake8 static analysis and Gemini AI review
- `comment_service` — posts inline review comments back to GitHub PRs
- `dashboard` — Flask + Chart.js dashboard showing review history and stats
- `shared/` — common DB schema (SQLAlchemy), Pydantic models, and config
- Shadow mode — full review pipeline without posting to GitHub
- `run_all.sh` — starts all four services in the background
