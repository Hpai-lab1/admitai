-- Module C (Experience & Operations): schema `app`.
-- Every table carries tenant_id and has Row-Level Security FORCED, so even a bug in the API
-- cannot read another college's rows. The API connects as `admitai_app` (no BYPASSRLS, not the
-- owner) and sets the request context with set_config(..., true) inside each transaction:
--   app.tenant_id    uuid of the college
--   app.role         college_admin | admission_officer | faculty | marketing_manager |
--                    visitor | service | login
--   app.profile_id   staff profile uuid (staff only)
--   app.departments  comma-separated departments (faculty only)
--   app.session_id   visitor session uuid (visitor only)

create schema if not exists app;

-- ------------------------------------------------------------------ context helpers
create or replace function app.current_tenant() returns uuid language sql stable as
$$ select nullif(current_setting('app.tenant_id', true), '')::uuid $$;

create or replace function app.my_role() returns text language sql stable as
$$ select coalesce(nullif(current_setting('app.role', true), ''), 'none') $$;

create or replace function app.current_profile() returns uuid language sql stable as
$$ select nullif(current_setting('app.profile_id', true), '')::uuid $$;

create or replace function app.current_session() returns uuid language sql stable as
$$ select nullif(current_setting('app.session_id', true), '')::uuid $$;

create or replace function app.current_departments() returns text[] language sql stable as
$$ select coalesce(string_to_array(nullif(current_setting('app.departments', true), ''), ','), '{}') $$;

create or replace function app.is_staff(variadic roles text[]) returns boolean language sql stable as
$$ select app.my_role() = any(roles) $$;

-- ------------------------------------------------------------------ tenancy and identity
create table app.tenants (
    id          uuid primary key default gen_random_uuid(),
    slug        text not null unique,
    name        text not null,
    public_key  text not null unique,           -- widget key; not a secret
    status      text not null default 'live' check (status in ('onboarding', 'live', 'suspended')),
    config      jsonb not null default '{}',     -- branding, starter questions, office hours, ...
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table app.profiles (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      uuid not null references app.tenants(id),
    email          text not null,
    full_name      text not null,
    role           text not null check (role in
                   ('super_admin', 'college_admin', 'admission_officer', 'faculty', 'marketing_manager')),
    password_hash  text not null,
    mfa_secret     text,
    mfa_enabled    boolean not null default false,
    is_active      boolean not null default true,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (tenant_id, email)
);

create table app.profile_departments (
    tenant_id   uuid not null references app.tenants(id),
    profile_id  uuid not null references app.profiles(id) on delete cascade,
    department  text not null,
    primary key (profile_id, department)
);

create table app.refresh_tokens (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references app.tenants(id),
    profile_id  uuid not null references app.profiles(id) on delete cascade,
    token_hash  text not null unique,
    expires_at  timestamptz not null,
    revoked_at  timestamptz,
    created_at  timestamptz not null default now()
);

create table app.visitor_sessions (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references app.tenants(id),
    channel     text not null default 'web_chat' check (channel in ('web_chat', 'whatsapp', 'web_voice')),
    utm         jsonb not null default '{}',
    referrer    text,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);

-- ------------------------------------------------------------------ audit (append-only)
create table app.audit_logs (
    id          bigserial primary key,
    tenant_id   uuid not null references app.tenants(id),
    actor_id    uuid,
    actor_kind  text not null check (actor_kind in ('staff', 'service', 'visitor', 'system')),
    action      text not null,
    entity      text not null,
    entity_id   text,
    before      jsonb,
    after       jsonb,
    at          timestamptz not null default now()
);

create or replace function app.audit_is_append_only() returns trigger language plpgsql as
$$ begin raise exception 'audit_logs is append-only'; end $$;
create trigger audit_no_update before update or delete on app.audit_logs
    for each row execute function app.audit_is_append_only();

-- ------------------------------------------------------------------ handoffs and callbacks
create table app.handoffs (
    id                 uuid primary key default gen_random_uuid(),
    tenant_id          uuid not null references app.tenants(id),
    conversation_id    text not null,
    session_id         uuid references app.visitor_sessions(id),
    queue              text not null check (queue in ('admission', 'faculty')),
    department         text,
    kind               text not null default 'callback' check (kind in ('handoff', 'callback')),
    reason             text not null,
    status             text not null default 'open' check (status in ('open', 'claimed', 'resolved')),
    language           text not null default 'en',
    context            jsonb not null default '{}',   -- transcript, ai_draft, sources, trace_id
    preferred_time     text,
    preferred_channel  text check (preferred_channel in ('phone', 'email', 'whatsapp')),
    lead_id            uuid,                          -- contact details live in leads, not here
    assigned_to        uuid references app.profiles(id),
    sla_due_at         timestamptz not null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    resolved_at        timestamptz,
    check (queue = 'admission' or department is not null)
);

create table app.handoff_events (
    id          bigserial primary key,
    tenant_id   uuid not null references app.tenants(id),
    handoff_id  uuid not null references app.handoffs(id) on delete cascade,
    actor_id    uuid,
    kind        text not null check (kind in ('created', 'claimed', 'replied', 'resolved', 'reopened')),
    body        text,
    at          timestamptz not null default now()
);

-- ------------------------------------------------------------------ consent and leads
create table app.consents (
    id                        uuid primary key default gen_random_uuid(),
    tenant_id                 uuid not null references app.tenants(id),
    session_id                uuid not null references app.visitor_sessions(id),
    lead_id                   uuid,
    purpose                   text not null check (purpose in ('contact', 'marketing', 'audio_recording', 'whatsapp')),
    granted                   boolean not null,
    given_by                  text not null default 'self' check (given_by in ('self', 'guardian')),
    guardian_otp_verified_at  timestamptz,
    text_version              text not null,
    channel                   text not null default 'web_chat',
    captured_at               timestamptz not null default now(),
    withdrawn_at              timestamptz,
    check (given_by = 'self' or guardian_otp_verified_at is not null)
);

create table app.guardian_verifications (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    uuid not null references app.tenants(id),
    session_id   uuid not null references app.visitor_sessions(id),
    channel      text not null check (channel in ('email', 'whatsapp')),
    destination  text not null,          -- the guardian's e-mail or WhatsApp number
    code_hash    text not null,
    attempts     int not null default 0,
    expires_at   timestamptz not null,
    verified_at  timestamptz,
    created_at   timestamptz not null default now()
);

create table app.leads (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         uuid not null references app.tenants(id),
    session_id        uuid not null references app.visitor_sessions(id),
    name              text,
    phone             text,
    email             text,
    city              text,
    program_interest  text[] not null default '{}',
    qualification     text,
    is_minor          boolean not null default false,
    guardian_contact  jsonb,
    score             int not null default 0 check (score between 0 and 100),
    band              text not null default 'cold' check (band in ('hot', 'warm', 'cold')),
    stage             text not null default 'new' check (stage in ('new', 'contacted', 'applied', 'admitted', 'lost')),
    source            jsonb not null default '{}',
    owner_id          uuid references app.profiles(id),
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    anonymised_at     timestamptz,
    -- PRD F5.3: never store an under-18 enquirer's own contact details
    check (not is_minor or (phone is null and email is null))
);

-- PRD F5.3: a minor's lead exists only after a guardian has verified consent (defence in depth;
-- the API checks this too).
create or replace function app.leads_require_guardian() returns trigger language plpgsql as
$$
begin
    if new.is_minor and not exists (
        select 1 from app.consents c
        where c.session_id = new.session_id and c.purpose = 'contact' and c.granted
          and c.given_by = 'guardian' and c.guardian_otp_verified_at is not null and c.withdrawn_at is null
    ) then
        raise exception 'a lead for a minor needs verified guardian consent';
    end if;
    return new;
end
$$;
create trigger leads_guardian_check before insert on app.leads
    for each row execute function app.leads_require_guardian();

create table app.lead_events (
    id         bigserial primary key,
    tenant_id  uuid not null references app.tenants(id),
    lead_id    uuid not null references app.leads(id) on delete cascade,
    actor_id   uuid,
    kind       text not null,
    detail     jsonb not null default '{}',
    at         timestamptz not null default now()
);

-- ------------------------------------------------------------------ feedback and corrections
create table app.feedback (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        uuid not null references app.tenants(id),
    conversation_id  text not null,
    message_id       text not null,
    kind             text not null check (kind in ('thumb', 'rating')),
    value            int not null,
    reason           text check (reason in ('wrong', 'outdated', 'incomplete', 'not_what_i_asked',
                                            'hard_to_understand', 'other')),
    comment          text,
    actor            text not null check (actor in ('visitor', 'staff')),
    channel          text not null default 'web_chat',
    trace_id         text,
    created_at       timestamptz not null default now(),
    check ((kind = 'thumb' and value in (-1, 1)) or (kind = 'rating' and value between 1 and 5))
);

create table app.corrections (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         uuid not null references app.tenants(id),
    conversation_id   text not null,
    message_id        text not null,
    original_answer   text,
    corrected_answer  text not null,
    reason            text not null,
    scope             text not null default 'general' check (scope in ('this_answer', 'general')),
    author_id         uuid not null references app.profiles(id),
    status            text not null default 'submitted' check (status in ('submitted', 'accepted', 'rejected')),
    trace_id          text,
    created_at        timestamptz not null default now()
);

-- ------------------------------------------------------------------ events to Module A, notifications
create table app.outbox_events (
    id            bigserial primary key,
    tenant_id     uuid not null references app.tenants(id),
    type          text not null check (type in ('feedback.created', 'correction.submitted')),
    payload       jsonb not null,
    created_at    timestamptz not null default now(),
    delivered_at  timestamptz,
    attempts      int not null default 0,
    last_error    text
);

create table app.notifications (
    id          bigserial primary key,
    tenant_id   uuid not null references app.tenants(id),
    audience    text not null,       -- 'admission', 'faculty:<department>', 'college_admin'
    kind        text not null,
    payload     jsonb not null default '{}',
    created_at  timestamptz not null default now(),
    read_by     uuid[] not null default '{}'
);

-- Messages that would be e-mailed or sent by WhatsApp (guardian codes, callback acknowledgements).
-- Local/dev stand-in until a real provider is configured; never holds codes in clear text.
create table app.message_outbox (
    id          bigserial primary key,
    tenant_id   uuid not null references app.tenants(id),
    channel     text not null check (channel in ('email', 'whatsapp', 'sms')),
    template    text not null,
    destination text not null,
    created_at  timestamptz not null default now(),
    sent_at     timestamptz
);

-- ------------------------------------------------------------------ lookups before a tenant is known
-- Widget session start (by public key) and staff sign-in (by slug) happen before app.tenant_id is set.
create or replace function app.tenant_by_public_key(key text)
returns table (id uuid, slug text, name text, config jsonb)
language sql stable security definer set search_path = app, pg_temp as
$$ select id, slug, name, config from app.tenants where public_key = key and status <> 'suspended' $$;

create or replace function app.tenant_by_slug(s text)
returns table (id uuid, slug text, name text, config jsonb)
language sql stable security definer set search_path = app, pg_temp as
$$ select id, slug, name, config from app.tenants where slug = s and status <> 'suspended' $$;

-- ------------------------------------------------------------------ indexes
create index on app.handoffs (tenant_id, status, queue, department);
create index on app.leads (tenant_id, stage, band);
create index on app.feedback (tenant_id, created_at);
create index on app.audit_logs (tenant_id, at);
create index on app.outbox_events (delivered_at) where delivered_at is null;
