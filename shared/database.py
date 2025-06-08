"""
CodeSheriff – Shared Database Layer
Defines the SQLAlchemy engine, session factory, ORM models, and helper
utilities used by every service that needs to log or query reviews.
"""

import json
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.config import DATABASE_URL

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    # Required for SQLite when used from multiple threads (FastAPI worker pool)
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ReviewLog – one row per PR review attempt
# ---------------------------------------------------------------------------
class ReviewLog(Base):
    """Persists every review that CodeSheriff processes.

    Fields
    ------
    id             Auto-increment primary key.
    pr_id          GitHub pull-request number.
    repo           Repository slug in "owner/name" format.
    owner          Repository owner (GitHub username or org).
    head_sha       Commit SHA of the PR head at review time.
    timestamp      UTC time the review was triggered.
    mode           "shadow" – logged only  |  "live" – also posted to GitHub.
    status         "pending" → "completed" | "failed".
    findings_count Number of issues found by static analysis + AI combined.
    findings_json  JSON-serialised list of Finding dicts for later inspection.
    """

    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, index=True)
    pr_id = Column(Integer, nullable=False, index=True)
    repo = Column(String(255), nullable=False)
    owner = Column(String(255), nullable=False)
    head_sha = Column(String(40), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    mode = Column(String(10), nullable=False)        # "shadow" | "live"
    status = Column(String(20), default="pending")   # "pending" | "completed" | "failed"
    findings_count = Column(Integer, default=0)
    findings_json = Column(Text, nullable=True)      # JSON array of Finding objects

    # Convenience helpers ------------------------------------------------

    def set_findings(self, findings: list[dict]) -> None:
        """Serialise a list of finding dicts and update the counter."""
        self.findings_json = json.dumps(findings)
        self.findings_count = len(findings)

    def get_findings(self) -> list[dict]:
        """Deserialise stored findings; returns [] when not yet set."""
        if not self.findings_json:
            return []
        return json.loads(self.findings_json)


# ---------------------------------------------------------------------------
# WebhookEvent – one row per incoming GitHub webhook call
# ---------------------------------------------------------------------------
class WebhookEvent(Base):
    """Records every webhook GitHub delivers, including ignored ones.

    Fields
    ------
    id           Auto-increment primary key.
    received_at  UTC time the request arrived.
    event_type   Value of X-GitHub-Event header (e.g. "pull_request", "ping").
    action       Payload action field (e.g. "opened", "synchronize", "closed").
    repo         Repository slug in "owner/name" format (empty for non-PR events).
    pr_number    GitHub PR number (0 for non-PR events).
    sender       GitHub login of the user who triggered the event.
    outcome      What CodeSheriff did: "queued" | "ignored" | "rejected" | "error".
    review_log_id  FK to review_logs.id – set only when outcome is "queued".
    payload_json Raw JSON payload (truncated to 8 KB to keep the DB lean).
    """

    __tablename__ = "webhook_events"

    id            = Column(Integer, primary_key=True, index=True)
    received_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    event_type    = Column(String(64),  nullable=False, default="")
    action        = Column(String(64),  nullable=False, default="")
    repo          = Column(String(255), nullable=False, default="")
    pr_number     = Column(Integer,     nullable=False, default=0)
    sender        = Column(String(128), nullable=False, default="")
    outcome       = Column(String(32),  nullable=False, default="")
    review_log_id = Column(Integer,     nullable=True)
    payload_json  = Column(Text,        nullable=True)   # ≤ 8 KB snapshot

    def set_payload(self, raw: bytes | dict) -> None:
        """Store a compact JSON snapshot, capped at 8 KB."""
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = json.dumps(raw, separators=(",", ":"))
        self.payload_json = text[:8192]

    def get_payload(self) -> dict:
        """Return the stored payload as a dict (empty dict on parse error)."""
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except json.JSONDecodeError:
            return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_tables() -> None:
    """Create all ORM tables (idempotent – safe to call on every startup)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI / Flask dependency that yields a DB session and closes it."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
