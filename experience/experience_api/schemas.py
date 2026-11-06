"""Request and response models. FastAPI turns these into the OpenAPI document that is proposed
for contracts/experience.openapi.yaml (see experience/contract-proposal/)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal[
    "super_admin", "college_admin", "admission_officer", "faculty", "marketing_manager"
]
Queue = Literal["admission", "faculty"]
ContactChannel = Literal["phone", "email", "whatsapp"]
Purpose = Literal["contact", "marketing", "audio_recording", "whatsapp"]
Stage = Literal["new", "contacted", "applied", "admitted", "lost"]
Band = Literal["hot", "warm", "cold"]
FeedbackReason = Literal[
    "wrong", "outdated", "incomplete", "not_what_i_asked", "hard_to_understand", "other"
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164


def _clean(v: str | None) -> str | None:
    return " ".join(v.split()) if isinstance(v, str) and v.strip() else None


def check_email(v: str | None) -> str | None:
    v = _clean(v)
    if v is not None and not EMAIL_RE.match(v):
        raise ValueError("Enter an e-mail address like name@example.com")
    return v.lower() if v else v


def check_phone(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    v = re.sub(r"[\s-]", "", v)
    if not PHONE_RE.match(v):
        raise ValueError(
            "Enter the number with country code, for example +91 followed by 10 digits"
        )
    return v


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------ auth
class LoginIn(Model):
    tenant: str = Field(
        min_length=1, max_length=60, description="College slug, e.g. demo-college"
    )
    email: str = Field(max_length=200)
    password: str = Field(min_length=1, max_length=200)


class MfaIn(Model):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class RefreshIn(Model):
    refresh_token: str


class PasswordChangeIn(Model):
    current_password: str
    new_password: str = Field(max_length=200)


class Tokens(BaseModel):
    status: Literal["ok"] = "ok"
    access_token: str
    refresh_token: str
    expires_in: int
    role: Role
    name: str
    departments: list[str]


class MfaChallenge(BaseModel):
    status: Literal["mfa_required", "mfa_enroll"]
    mfa_token: str
    otpauth_uri: str | None = None
    secret: str | None = None


class Me(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role
    departments: list[str]
    mfa_enabled: bool
    tenant: dict


# ------------------------------------------------------------------ widget
class WidgetSessionIn(Model):
    public_key: str = Field(min_length=8, max_length=120)
    channel: Literal["web_chat", "web_voice"] = "web_chat"
    utm: dict[str, str] = Field(default_factory=dict)
    referrer: str | None = Field(default=None, max_length=500)

    @field_validator("utm")
    @classmethod
    def utm_keys(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
        }
        return {k: str(val)[:200] for k, val in v.items() if k in allowed}


class WidgetSession(BaseModel):
    session_id: UUID
    session_token: str
    expires_at: datetime
    tenant: dict


# ------------------------------------------------------------------ handoffs and callbacks
class TranscriptTurn(Model):
    role: Literal["user", "assistant", "staff", "system"]
    text: str = Field(max_length=4000)


class Source(Model):
    title: str = Field(max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    section: str | None = Field(default=None, max_length=300)
    academic_year: str | None = Field(default=None, max_length=20)


class HandoffContext(Model):
    """PRD F3.3 handoff package."""

    transcript: list[TranscriptTurn] = Field(default_factory=list, max_length=60)
    ai_draft: str | None = Field(default=None, max_length=4000)
    sources: list[Source] = Field(default_factory=list, max_length=20)
    trace_id: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)


class HandoffIn(Model):
    """Called by Module B (PRD F3.4) when a question needs a person."""

    conversation_id: str = Field(min_length=1, max_length=100)
    session_id: UUID | None = None
    queue: Queue
    department: str | None = Field(default=None, max_length=80)
    kind: Literal["handoff", "callback"] = "callback"
    reason: str = Field(min_length=1, max_length=300)
    language: str = Field(default="en", max_length=10)
    context: HandoffContext = Field(default_factory=HandoffContext)
    preferred_time: str | None = Field(default=None, max_length=100)
    preferred_channel: ContactChannel | None = None
    lead_id: UUID | None = None


class CallbackIn(Model):
    """'Talk to a person' from the widget (PRD F1.7, F3.4). Needs contact consent first."""

    conversation_id: str = Field(min_length=1, max_length=100)
    queue: Queue = "admission"
    department: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="Asked to talk to a person", max_length=300)
    preferred_time: str | None = Field(default=None, max_length=100)
    preferred_channel: ContactChannel = "phone"
    language: str = Field(default="en", max_length=10)
    transcript: list[TranscriptTurn] = Field(default_factory=list, max_length=60)


class Handoff(BaseModel):
    id: UUID
    conversation_id: str
    queue: Queue
    department: str | None
    kind: str
    reason: str
    status: Literal["open", "claimed", "resolved"]
    language: str
    context: dict
    preferred_time: str | None
    preferred_channel: str | None
    lead_id: UUID | None
    assigned_to: UUID | None
    assigned_name: str | None = None
    sla_due_at: datetime
    overdue: bool = False
    created_at: datetime
    lead: dict | None = None
    events: list[dict] = Field(default_factory=list)
    feedback: list[dict] = Field(default_factory=list)


class CallbackAck(BaseModel):
    handoff_id: UUID
    expected_response_by: datetime
    message: str


class ReplyIn(Model):
    body: str = Field(min_length=1, max_length=4000)
    as_correction: bool = Field(
        default=False, description="Offer this reply as a correction (PRD F3.6)"
    )
    message_id: str | None = Field(default=None, max_length=100)
    send_email: bool = Field(
        default=False,
        description="Also e-mail the reply to the applicant, or a minor's guardian (PRD F3.6)",
    )
    send_whatsapp: bool = Field(
        default=False,
        description="Also send the reply on WhatsApp through Module B: the applicant's number, or"
        " a minor's guardian who verified on WhatsApp (PRD F3.6). Outside the 24-hour window only"
        " the college's approved template can be sent.",
    )


# ------------------------------------------------------------------ consent and leads
class ConsentIn(Model):
    purpose: Purpose
    granted: bool
    text_version: str = Field(
        min_length=1, max_length=40, description="Version of the consent text shown"
    )
    channel: str = Field(default="web_chat", max_length=20)


class Consent(BaseModel):
    id: UUID
    purpose: Purpose
    granted: bool
    given_by: Literal["self", "guardian"]
    text_version: str
    captured_at: datetime
    withdrawn_at: datetime | None


class GuardianStartIn(Model):
    channel: Literal["email", "whatsapp"]
    destination: str = Field(
        max_length=200,
        description="Guardian's e-mail, or WhatsApp number with country code",
    )
    language: Literal["en", "hi"] = Field(
        default="en", description="Language of the WhatsApp code message"
    )


class GuardianStart(BaseModel):
    verification_id: UUID
    expires_at: datetime
    message: str
    dev_code: str | None = Field(
        default=None, description="Only when DEV_ECHO_CODES=1 (local development)"
    )


class GuardianVerifyIn(Model):
    verification_id: UUID
    code: str = Field(min_length=4, max_length=8)
    purposes: list[Literal["contact", "marketing"]] = Field(
        default_factory=lambda: ["contact"], min_length=1
    )
    text_version: str = Field(min_length=1, max_length=40)


class WithdrawIn(Model):
    purposes: list[Purpose] = Field(
        default_factory=lambda: ["contact", "marketing", "audio_recording", "whatsapp"]
    )


class LeadIn(Model):
    """Created by Module B (conversational capture) or the widget's lead card, only after consent."""

    name: str | None = Field(default=None, max_length=120)
    phone: str | None = None
    email: str | None = None
    city: str | None = Field(default=None, max_length=80)
    program_interest: list[str] = Field(default_factory=list, max_length=10)
    qualification: str | None = Field(default=None, max_length=120)
    is_minor: bool = False
    preferred_time: str | None = Field(default=None, max_length=100)
    score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="From Module B's scoring rules (PRD F5.4)",
    )
    conversation_id: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str | None) -> str | None:
        return check_email(v)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v: str | None) -> str | None:
        return check_phone(v)


class Lead(BaseModel):
    id: UUID
    name: str | None
    phone: str | None
    email: str | None
    city: str | None
    program_interest: list[str]
    qualification: str | None
    is_minor: bool
    guardian_contact: dict | None
    score: int
    band: Band
    stage: Stage
    source: dict
    owner_id: UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    anonymised: bool = False


class LeadPatch(Model):
    stage: Stage | None = None
    notes: str | None = Field(default=None, max_length=2000)
    owner_id: UUID | None = None


class LeadList(BaseModel):
    items: list[Lead]
    total: int


# ------------------------------------------------------------------ feedback and corrections
class FeedbackIn(Model):
    conversation_id: str = Field(min_length=1, max_length=100)
    message_id: str = Field(min_length=1, max_length=100)
    kind: Literal["thumb", "rating"]
    value: int = Field(description="thumb: 1 or -1; rating: 1-5")
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)
    trace_id: str | None = Field(default=None, max_length=100)
    channel: Literal["web_chat", "whatsapp", "web_voice"] = "web_chat"
    department: str | None = Field(
        default=None, max_length=80, description="Department of the answer (from B)"
    )

    @field_validator("value")
    @classmethod
    def value_range(cls, v: int, info) -> int:
        kind = info.data.get("kind")
        if kind == "thumb" and v not in (-1, 1):
            raise ValueError("A thumb is 1 (helpful) or -1 (not helpful)")
        if kind == "rating" and not 1 <= v <= 5:
            raise ValueError("A rating is 1 to 5")
        return v


class Created(BaseModel):
    id: UUID


class CorrectionIn(Model):
    conversation_id: str = Field(min_length=1, max_length=100)
    message_id: str = Field(min_length=1, max_length=100)
    original_answer: str | None = Field(default=None, max_length=4000)
    corrected_answer: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=500)
    scope: Literal["this_answer", "general"] = "general"
    trace_id: str | None = Field(default=None, max_length=100)


# ------------------------------------------------------------------ approvals (Module A proposals)
class PeakSeasonIn(Model):
    peak_season: bool


class DecideIn(Model):
    ids: list[str] = Field(min_length=1, max_length=50)
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class SecondReviewIn(Model):
    """PRD 8.3: a second approver's verdict on a sampled, already approved proposal."""

    verdict: Literal["agree", "disagree"]
    note: str | None = Field(default=None, max_length=1000)


class DelegatesIn(Model):
    """PRD 8.3: who gets an overdue proposal before the College Admin. Keys are departments, or
    'default' for college-wide proposals; values name a person."""

    delegates: dict[str, str] = Field(max_length=50)


# ------------------------------------------------------------------ admin
class UserIn(Model):
    email: str
    full_name: str = Field(min_length=1, max_length=120)
    role: Literal["college_admin", "admission_officer", "faculty", "marketing_manager"]
    departments: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        checked = check_email(v)
        if not checked:
            raise ValueError("E-mail is required")
        return checked


class UserPatch(Model):
    full_name: str | None = Field(default=None, max_length=120)
    role: (
        Literal["college_admin", "admission_officer", "faculty", "marketing_manager"]
        | None
    ) = None
    departments: list[str] | None = Field(default=None, max_length=20)
    is_active: bool | None = None
    reset_mfa: bool = False


class User(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role
    departments: list[str]
    is_active: bool
    mfa_enabled: bool
    created_at: datetime


class UserCreated(User):
    temporary_password: str = Field(
        description="Shown once; the person changes it after first sign-in"
    )


class TenantConfig(Model):
    """Per-college settings from the Tenant Pack that Module C uses (PRD F10.1)."""

    assistant_name: str = Field(default="Admissions Assistant", max_length=60)
    greeting: str = Field(
        default="Hello! Ask me anything about admissions.", max_length=300
    )
    primary_color: str = Field(default="#1E3A8A", pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_url: str | None = Field(default=None, max_length=500)
    starter_questions: list[str] = Field(default_factory=list, max_length=8)
    departments: list[str] = Field(default_factory=list, max_length=40)
    response_time_hours: int = Field(default=24, ge=1, le=168)
    office_hours: str = Field(default="Mon-Sat 09:00-17:00", max_length=100)
    consent_text_version: str = Field(default="2026-10-v1", max_length=40)
    privacy_notice_url: str | None = Field(default=None, max_length=500)
    # Theme and website features, so the widget matches each college's own site
    secondary_color: str = Field(default="#3B82F6", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#111827", pattern=r"^#[0-9A-Fa-f]{6}$")
    font: Literal["system", "montserrat", "inter", "noto-sans"] = "system"
    apply_url: str | None = Field(default=None, max_length=500)
    announcement: str | None = Field(default=None, max_length=200)
    topics: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Quick topic chips, like the admissions menu on the college site",
    )


# ------------------------------------------------------------------ conversation events, break-glass
class ConversationEventIn(Model):
    """Implicit signals (PRD F8.3) and the counts behind the 3.5 metric definitions."""

    conversation_id: str = Field(min_length=1, max_length=100)
    kind: Literal[
        "user_message",
        "answer",
        "rephrase",
        "talk_to_human",
        "voice_hangup",
        "barge_in",
    ]
    message_id: str | None = Field(default=None, max_length=100)
    channel: Literal["web_chat", "whatsapp", "web_voice"] = "web_chat"


class BreakGlassIn(Model):
    reason: str = Field(min_length=10, max_length=500)
