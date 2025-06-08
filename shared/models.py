"""
CodeSheriff – Shared Pydantic Models
These data-transfer objects are the public contract between services.
Import from this module to keep types consistent across webhook-, analysis-,
and comment-service.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Severity levels a finding can carry."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReviewMode(str, Enum):
    """Whether a completed review was posted or only logged."""
    SHADOW = "shadow"
    LIVE = "live"


# ---------------------------------------------------------------------------
# Core data shapes
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """A single issue discovered by static analysis or AI review."""

    file: str = Field(..., description="Relative path of the affected file inside the repo")
    line: int = Field(..., ge=1, description="1-based line number of the issue")
    severity: Severity = Field(..., description="error | warning | info")
    suggestion: str = Field(..., description="Human-readable explanation and fix hint")
    source: str = Field(default="ai", description="'flake8' or 'ai' – origin of the finding")


# ---------------------------------------------------------------------------
# Service request / response schemas
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    """Payload that webhook-service sends to analysis-service."""

    pr_number: int = Field(..., description="GitHub PR number")
    repo: str = Field(..., description="Repository slug, e.g. 'owner/my-repo'")
    owner: str = Field(..., description="GitHub username or organisation")
    head_sha: str = Field(..., description="Commit SHA at the PR head")
    shadow_mode: bool = Field(
        default=False,
        description="When True the review is logged but NOT posted to GitHub",
    )


class ReviewResult(BaseModel):
    """Payload that analysis-service returns (and comment-service accepts)."""

    pr_number: int
    repo: str
    owner: str
    head_sha: str
    shadow_mode: bool
    findings: list[Finding] = Field(default_factory=list)
    summary: Optional[str] = Field(None, description="AI-generated overall assessment")


class CommentRequest(BaseModel):
    """Payload that analysis-service sends to comment-service."""

    review: ReviewResult
    review_log_id: int = Field(..., description="DB row ID so the comment-service can update status")


class HealthResponse(BaseModel):
    """Standard health-check response shared by all services."""

    service: str
    status: str = "ok"
