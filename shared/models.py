from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReviewMode(str, Enum):
    SHADOW = "shadow"
    LIVE = "live"


class Finding(BaseModel):
    file: str
    line: int = Field(..., ge=1)
    severity: Severity
    suggestion: str
    source: str = Field(default="ai")


class ReviewRequest(BaseModel):
    pr_number: int
    repo: str
    owner: str
    head_sha: str
    shadow_mode: bool = False


class ReviewResult(BaseModel):
    pr_number: int
    repo: str
    owner: str
    head_sha: str
    shadow_mode: bool
    findings: list[Finding] = Field(default_factory=list)
    summary: Optional[str] = None


class CommentRequest(BaseModel):
    review: ReviewResult
    review_log_id: int


class HealthResponse(BaseModel):
    service: str
    status: str = "ok"
