"""Consent (DPDP, PRD F5.1-F5.3), guardian verification for under-18 enquirers, and leads.

Rules enforced here and again in the database:
- no lead without a granted, unwithdrawn contact consent for that visitor session;
- a minor's own phone/e-mail is never stored; only the verified guardian contact is kept;
- withdrawing contact consent anonymises the session's leads at once."""

from __future__ import annotations

import csv
import io
import smtplib
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from .. import messaging
from ..clients import AssistantRefused, UpstreamError, assistant_post
from ..config import Settings
from ..webhooks import cancel_for_lead, emit, lead_data
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, settings_dep, visitor_context, visitor_or_service
from ..schemas import (
    Consent,
    ConsentIn,
    GuardianStart,
    GuardianStartIn,
    GuardianVerifyIn,
    Lead,
    LeadIn,
    LeadList,
    LeadPatch,
    WithdrawIn,
    check_email,
    check_phone,
)
from ..scoring import band, fallback_score
from ..security import hash_code, new_numeric_code
from .handoffs import has_contact_consent

router = APIRouter()
MAX_CODE_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5
LEAD_READERS = require_staff(
    "super_admin", "college_admin", "admission_officer", "marketing_manager", "faculty"
)
LEAD_EDITORS = require_staff("college_admin", "admission_officer")
EXPORTERS = require_staff("college_admin", "admission_officer", "marketing_manager")


def _need_session(ctx: Context) -> UUID:
    if not ctx.session_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Session-Id is required for this call"
        )
    return ctx.session_id


def _consent(row: dict) -> Consent:
    return Consent(**{k: row[k] for k in Consent.model_fields})


# ------------------------------------------------------------------ consent
@router.post(
    "/consents",
    response_model=Consent,
    status_code=201,
    tags=["consent"],
    summary="Record the visitor's own consent for one purpose",
)
def record_consent(
    body: ConsentIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    sid = _need_session(ctx)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.consents (tenant_id, session_id, purpose, granted, given_by, text_version, channel)"
            " values (%s, %s, %s, %s, 'self', %s, %s) returning *",
            (
                ctx.tenant_id,
                sid,
                body.purpose,
                body.granted,
                body.text_version,
                body.channel,
            ),
        )
        row = cur.fetchone()
        audit(
            cur,
            ctx,
            "consent.recorded",
            "consent",
            row["id"],
            after={
                "purpose": body.purpose,
                "granted": body.granted,
                "text_version": body.text_version,
            },
        )
        return _consent(row)


@router.get(
    "/consents",
    response_model=list[Consent],
    tags=["consent"],
    summary="This visitor's consents",
)
def my_consents(
    ctx: Context = Depends(visitor_context), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select * from app.consents where session_id = %s order by captured_at",
            (ctx.session_id,),
        )
        return [_consent(r) for r in cur.fetchall()]


@router.post(
    "/consents/guardian/start",
    response_model=GuardianStart,
    status_code=201,
    tags=["consent"],
    summary="Send a one-time code to the guardian of an under-18 enquirer (PRD F5.3)",
)
def guardian_start(
    body: GuardianStartIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    sid = _need_session(ctx)
    try:
        destination = (
            check_email(body.destination)
            if body.channel == "email"
            else check_phone(body.destination)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if not destination:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the guardian's contact"
        )
    # Never claim a code was sent when nothing can send it. E-mail needs SMTP settings; WhatsApp
    # needs Module B, which holds the code until the guardian's own number writes to the college
    # (guardian-initiated, so the college never messages a number nobody has confirmed).
    if body.channel == "email":
        can_send = messaging.email_configured(settings)
    else:
        can_send = bool(settings.key_to_assistant)
    if not can_send and not settings.dev_echo_codes:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Codes by WhatsApp are not available right now; please use the parent's or"
            " guardian's e-mail"
            if body.channel == "whatsapp"
            else "We cannot send e-mail right now; please ask for a callback instead",
        )
    code = new_numeric_code()
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.guardian_code_minutes
    )
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select count(*) as n from app.guardian_verifications where session_id = %s"
            " and created_at > now() - interval '1 hour'",
            (sid,),
        )
        if cur.fetchone()["n"] >= MAX_CODES_PER_HOUR:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many codes sent; try again in an hour",
            )
        cur.execute(
            "insert into app.guardian_verifications (tenant_id, session_id, channel, destination, code_hash,"
            " expires_at) values (%s, %s, %s, %s, 'pending', %s) returning id",
            (ctx.tenant_id, sid, body.channel, destination, expires),
        )
        vid = cur.fetchone()["id"]
        cur.execute(
            "update app.guardian_verifications set code_hash = %s where id = %s",
            (hash_code(code, str(vid)), vid),
        )
        audit(
            cur,
            ctx,
            "guardian.code_sent",
            "guardian_verification",
            vid,
            after={"channel": body.channel, "sent": can_send},
        )
        college = messaging.college_name(cur)
    message = "Development: the code is shown here instead of being sent."
    if can_send and body.channel == "whatsapp":
        # Module B keeps it sealed with a key derived from the guardian's number; C keeps only
        # the hash. If B cannot take it, say so rather than pretend.
        try:
            held = assistant_post(
                settings,
                "/channels/whatsapp/guardian-code",
                ctx.tenant_id,
                {
                    "destination": destination,
                    "code": code,
                    "expires_at": expires.isoformat(),
                    "language": body.language,
                },
            )
        except AssistantRefused as exc:
            if not settings.dev_echo_codes:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Codes by WhatsApp are not available for this college; please use the"
                    " parent's or guardian's e-mail",
                ) from None
            message = f"Development: Module B refused ({exc}); the code is shown here."
        except UpstreamError:
            if not settings.dev_echo_codes:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "We could not set up the WhatsApp code just now; please try again in a few"
                    " minutes or use the parent's or guardian's e-mail",
                ) from None
            message = "Development: Module B is unreachable; the code is shown here."
        else:
            number = held.get("display_number")
            message = (
                "Please ask your parent or guardian to send any message on WhatsApp from"
                f" {destination} to the college"
                + (f" at {number}" if number else "")
                + ". They will get the code in reply; it expires in"
                f" {settings.guardian_code_minutes} minutes."
            )
    elif can_send:
        # Sent now and stored nowhere (not even the message outbox); only its hash is kept.
        try:
            messaging.send(
                settings,
                destination,
                f"{college}: your verification code",
                f"Your child asked {college} about admissions and gave this address so that you can"
                f" agree to the college contacting you.\n\nYour code is {code}. It expires in"
                f" {settings.guardian_code_minutes} minutes.\n\nIf you did not expect this e-mail,"
                f" ignore it; nothing is shared without the code.\n",
            )
        except (OSError, smtplib.SMTPException):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "We could not send the code just now; please try again in a few minutes",
            ) from None
        message = "We sent a 6-digit code to the parent or guardian by e-mail."
    return GuardianStart(
        verification_id=vid,
        expires_at=expires,
        message=message,
        dev_code=code if settings.dev_echo_codes else None,
    )


@router.post(
    "/consents/guardian/verify",
    response_model=list[Consent],
    status_code=201,
    tags=["consent"],
    summary="Check the guardian's code and record guardian consent",
)
def guardian_verify(
    body: GuardianVerifyIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    sid = _need_session(ctx)
    problem: tuple[int, str] | None = None
    created: list[Consent] = []
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select * from app.guardian_verifications where id = %s and session_id = %s for update",
            (body.verification_id, sid),
        )
        v = cur.fetchone()
        if not v:
            problem = (status.HTTP_404_NOT_FOUND, "Start the guardian check again")
        elif v["verified_at"]:
            problem = (status.HTTP_409_CONFLICT, "This code was already used")
        elif v["expires_at"] < datetime.now(timezone.utc):
            problem = (status.HTTP_410_GONE, "The code has expired; send a new one")
        elif v["attempts"] >= MAX_CODE_ATTEMPTS:
            problem = (
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many wrong tries; send a new code",
            )
        elif v["code_hash"] != hash_code(body.code, str(v["id"])):
            # the attempt counter is saved even though the request fails
            cur.execute(
                "update app.guardian_verifications set attempts = attempts + 1 where id = %s",
                (v["id"],),
            )
            left = MAX_CODE_ATTEMPTS - v["attempts"] - 1
            problem = (
                status.HTTP_401_UNAUTHORIZED,
                f"That code is not right; {left} tries left",
            )
        else:
            cur.execute(
                "update app.guardian_verifications set verified_at = now() where id = %s returning verified_at",
                (v["id"],),
            )
            at = cur.fetchone()["verified_at"]
            for purpose in dict.fromkeys(body.purposes):
                cur.execute(
                    "insert into app.consents (tenant_id, session_id, purpose, granted, given_by,"
                    " guardian_otp_verified_at, text_version) values (%s, %s, %s, true, 'guardian', %s, %s)"
                    " returning *",
                    (ctx.tenant_id, sid, purpose, at, body.text_version),
                )
                created.append(_consent(cur.fetchone()))
            audit(
                cur,
                ctx,
                "guardian.verified",
                "guardian_verification",
                v["id"],
                after={"purposes": list(body.purposes)},
            )
    if problem:
        raise HTTPException(*problem)
    return created


@router.post(
    "/consents/withdraw",
    tags=["consent"],
    summary="Withdraw consent; withdrawing contact anonymises this visitor's leads at once",
)
def withdraw(
    body: WithdrawIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    sid = _need_session(ctx)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "update app.consents set withdrawn_at = now() where session_id = %s and purpose = any(%s)"
            " and withdrawn_at is null",
            (sid, list(body.purposes)),
        )
        withdrawn = cur.rowcount
        anonymised = 0
        if "contact" in body.purposes:
            anonymised = anonymise_session_leads(cur, ctx, sid)
        audit(
            cur,
            ctx,
            "consent.withdrawn",
            "visitor_session",
            sid,
            after={"purposes": list(body.purposes), "leads_anonymised": anonymised},
        )
    return {"withdrawn": withdrawn, "leads_anonymised": anonymised}


def anonymise_session_leads(cur, ctx: Context, session_id: UUID) -> int:
    cur.execute(
        "update app.leads set name = null, phone = null, email = null, city = null, qualification = null,"
        " guardian_contact = null, notes = null, anonymised_at = now(), updated_at = now()"
        " where session_id = %s and anonymised_at is null returning id",
        (session_id,),
    )
    ids = [r["id"] for r in cur.fetchall()]
    for lid in ids:
        cur.execute(
            "insert into app.lead_events (tenant_id, lead_id, actor_id, kind) values (%s, %s, %s, 'anonymised')",
            (ctx.tenant_id, lid, ctx.profile_id),
        )
        # Queued deliveries still carrying the person's details are dropped, and the college's
        # own systems are told to forget them too (no personal data in this event).
        cancel_for_lead(cur, lid)
        messaging.cancel_for_lead(cur, lid)
        emit(cur, "lead.updated", {"id": lid, "anonymised": True})
    return len(ids)


# ------------------------------------------------------------------ leads
def _lead(row: dict) -> Lead:
    data = {k: row[k] for k in Lead.model_fields if k in row}
    return Lead(**data, anonymised=row["anonymised_at"] is not None)


@router.post(
    "/leads",
    response_model=Lead,
    status_code=201,
    tags=["leads"],
    summary="Create or update this visitor's consented lead (called by Module B or the widget)",
)
def create_lead(
    body: LeadIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    sid = _need_session(ctx)
    if body.is_minor and (body.phone or body.email):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "For under-18s we only keep a parent's or guardian's contact, not the student's own",
        )
    with tx(settings.database_url, ctx) as cur:
        if not has_contact_consent(cur, sid):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Contact consent is needed before saving contact details",
            )
        guardian = None
        if body.is_minor:
            cur.execute(
                "select v.channel, v.destination from app.guardian_verifications v"
                " join app.consents c on c.session_id = v.session_id and c.given_by = 'guardian'"
                "  and c.purpose = 'contact' and c.granted and c.withdrawn_at is null"
                " where v.session_id = %s and v.verified_at is not null order by v.verified_at desc limit 1",
                (sid,),
            )
            g = cur.fetchone()
            if not g:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "A parent or guardian must confirm first"
                )
            guardian = {"channel": g["channel"], "destination": g["destination"]}
        elif not (body.phone or body.email):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Share a phone number or an e-mail address",
            )

        cur.execute(
            "select channel, utm, referrer from app.visitor_sessions where id = %s",
            (sid,),
        )
        s = cur.fetchone() or {}
        source = {
            "channel": s.get("channel"),
            "utm": s.get("utm") or {},
            "referrer": s.get("referrer"),
            "conversation_id": body.conversation_id,
            "via": ctx.role,
        }
        score = (
            body.score
            if body.score is not None
            else fallback_score(
                body.program_interest,
                bool(body.phone or guardian),
                bool(body.email),
                bool(body.qualification),
                bool(body.preferred_time),
            )
        )

        cur.execute(
            "select * from app.leads where session_id = %s and anonymised_at is null"
            " order by created_at desc limit 1 for update",
            (sid,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "update app.leads set name = coalesce(%s, name), phone = %s, email = %s,"
                " city = coalesce(%s, city), program_interest = %s, qualification = coalesce(%s, qualification),"
                " is_minor = %s, guardian_contact = %s, score = greatest(score, %s), band = %s,"
                " updated_at = now() where id = %s returning *",
                (
                    body.name,
                    None if body.is_minor else (body.phone or existing["phone"]),
                    None if body.is_minor else (body.email or existing["email"]),
                    body.city,
                    sorted(
                        set(existing["program_interest"]) | set(body.program_interest)
                    ),
                    body.qualification,
                    body.is_minor,
                    Jsonb(guardian) if guardian else None,
                    score,
                    band(max(existing["score"], score)),
                    existing["id"],
                ),
            )
            kind = "updated"
        else:
            cur.execute(
                "insert into app.leads (tenant_id, session_id, name, phone, email, city, program_interest,"
                " qualification, is_minor, guardian_contact, score, band, source)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
                (
                    ctx.tenant_id,
                    sid,
                    body.name,
                    body.phone,
                    body.email,
                    body.city,
                    body.program_interest,
                    body.qualification,
                    body.is_minor,
                    Jsonb(guardian) if guardian else None,
                    score,
                    band(score),
                    Jsonb(source),
                ),
            )
            kind = "created"
        row = cur.fetchone()
        cur.execute(
            "update app.consents set lead_id = %s where session_id = %s and lead_id is null",
            (row["id"], sid),
        )
        cur.execute(
            "insert into app.lead_events (tenant_id, lead_id, kind, detail) values (%s, %s, %s, %s)",
            (
                ctx.tenant_id,
                row["id"],
                kind,
                Jsonb({"score": row["score"], "via": ctx.role}),
            ),
        )
        audit(cur, ctx, f"lead.{kind}", "lead", row["id"])
        emit(cur, f"lead.{kind}", lead_data(row), lead_id=row["id"])
        return _lead(row)


def _lead_filters(stage, band_, q, since, include_anonymised) -> tuple[str, list]:
    where, params = ["true"], []
    if not include_anonymised:
        where.append("anonymised_at is null")
    if stage:
        where.append("stage = %s")
        params.append(stage)
    if band_:
        where.append("band = %s")
        params.append(band_)
    if q:
        where.append(
            "(name ilike %s or city ilike %s or array_to_string(program_interest, ' ') ilike %s)"
        )
        params += [f"%{q}%"] * 3
    if since:
        where.append("created_at >= %s")
        params.append(since)
    return " and ".join(where), params


@router.get(
    "/leads",
    response_model=LeadList,
    tags=["leads"],
    summary="Lead list with filters and pipeline stage",
)
def list_leads(
    stage: Literal["new", "contacted", "applied", "admitted", "lost"] | None = None,
    band_: Literal["hot", "warm", "cold"] | None = Query(None, alias="band"),
    q: str | None = Query(None, max_length=80),
    since: datetime | None = None,
    include_anonymised: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: Context = Depends(LEAD_READERS),
    settings: Settings = Depends(settings_dep),
):
    where, params = _lead_filters(stage, band_, q, since, include_anonymised)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(f"select count(*) as n from app.leads where {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"select * from app.leads where {where} order by score desc, created_at desc limit %s offset %s",
            (*params, limit, offset),
        )
        return LeadList(items=[_lead(r) for r in cur.fetchall()], total=total)


CSV_COLUMNS = [
    "id",
    "created_at",
    "name",
    "phone",
    "email",
    "city",
    "program_interest",
    "qualification",
    "is_minor",
    "guardian_contact",
    "score",
    "band",
    "stage",
    "utm_source",
    "utm_campaign",
]


def _cell(v) -> str:
    """Stop spreadsheet formula injection: text starting with = + - @ is prefixed with a quote."""
    text = "" if v is None else str(v)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get(
    "/leads/export",
    tags=["leads"],
    summary="CSV export for the CRM (anonymised leads are left out)",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_leads(
    stage: Literal["new", "contacted", "applied", "admitted", "lost"] | None = None,
    band_: Literal["hot", "warm", "cold"] | None = Query(None, alias="band"),
    since: datetime | None = None,
    ctx: Context = Depends(EXPORTERS),
    settings: Settings = Depends(settings_dep),
):
    where, params = _lead_filters(stage, band_, None, since, False)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            f"select * from app.leads where {where} order by created_at", params
        )
        rows = cur.fetchall()
        audit(
            cur,
            ctx,
            "leads.exported",
            "lead",
            None,
            after={"rows": len(rows), "stage": stage, "band": band_},
        )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        utm = (r["source"] or {}).get("utm") or {}
        guardian = r["guardian_contact"] or {}
        w.writerow(
            [
                _cell(x)
                for x in (
                    r["id"],
                    r["created_at"].isoformat(),
                    r["name"],
                    r["phone"],
                    r["email"],
                    r["city"],
                    "; ".join(r["program_interest"]),
                    r["qualification"],
                    r["is_minor"],
                    guardian.get("destination"),
                    r["score"],
                    r["band"],
                    r["stage"],
                    utm.get("utm_source"),
                    utm.get("utm_campaign"),
                )
            ]
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@router.patch(
    "/leads/{lead_id}",
    response_model=Lead,
    tags=["leads"],
    summary="Move a lead along the pipeline",
)
def update_lead(
    lead_id: UUID,
    body: LeadPatch,
    ctx: Context = Depends(LEAD_EDITORS),
    settings: Settings = Depends(settings_dep),
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to change")
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select * from app.leads where id = %s for update", (lead_id,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
        if before["anonymised_at"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This person withdrew consent; the lead is anonymised",
            )
        if "owner_id" in changes and changes["owner_id"]:
            cur.execute(
                "select 1 from app.staff_names() where id = %s", (changes["owner_id"],)
            )
            if not cur.fetchone():
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown staff member"
                )
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(
            f"update app.leads set {sets}, updated_at = now() where id = %s returning *",
            (*changes.values(), lead_id),
        )
        row = cur.fetchone()
        detail = {k: str(v) if isinstance(v, UUID) else v for k, v in changes.items()}
        cur.execute(
            "insert into app.lead_events (tenant_id, lead_id, actor_id, kind, detail) values (%s, %s, %s, %s, %s)",
            (
                ctx.tenant_id,
                lead_id,
                ctx.profile_id,
                "stage_changed" if "stage" in changes else "edited",
                Jsonb(detail),
            ),
        )
        audit(
            cur,
            ctx,
            "lead.updated",
            "lead",
            lead_id,
            before={
                k: str(before[k]) if before[k] is not None else None for k in changes
            },
            after=detail,
        )
        emit(cur, "lead.updated", lead_data(row), lead_id=lead_id)
        return _lead(row)
