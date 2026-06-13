import json
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, index=True)
    pr_id = Column(Integer, nullable=False, index=True)
    repo = Column(String(255), nullable=False)
    owner = Column(String(255), nullable=False)
    head_sha = Column(String(40), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    mode = Column(String(10), nullable=False)
    status = Column(String(20), default="pending")
    findings_count = Column(Integer, default=0)
    findings_json = Column(Text, nullable=True)

    def set_findings(self, findings: list[dict]) -> None:
        self.findings_json = json.dumps(findings)
        self.findings_count = len(findings)

    def get_findings(self) -> list[dict]:
        if not self.findings_json:
            return []
        return json.loads(self.findings_json)


class WebhookEvent(Base):
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
    payload_json  = Column(Text,        nullable=True)

    def set_payload(self, raw: bytes | dict) -> None:
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = json.dumps(raw, separators=(",", ":"))
        self.payload_json = text[:8192]

    def get_payload(self) -> dict:
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except json.JSONDecodeError:
            return {}


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
