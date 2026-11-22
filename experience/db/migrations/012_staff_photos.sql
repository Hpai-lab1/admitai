-- PRD 13: the visitor's handoff banner can show the photo of the staff member who claimed their
-- request. Staff add their own photo; nobody else's. Staff photos only, never applicant data.
create table if not exists app.profile_photos (
    profile_id    uuid primary key references app.profiles(id) on delete cascade,
    tenant_id     uuid not null references app.tenants(id),
    content_type  text not null check (content_type in ('image/png', 'image/jpeg', 'image/webp')),
    data          bytea not null check (octet_length(data) between 1 and 300000),
    updated_at    timestamptz not null default now()
);
alter table app.profile_photos enable row level security;
alter table app.profile_photos force row level security;
drop policy if exists own_photo on app.profile_photos;
create policy own_photo on app.profile_photos
    using (tenant_id = app.current_tenant() and profile_id = app.current_profile())
    with check (tenant_id = app.current_tenant() and profile_id = app.current_profile());

-- The photo of whoever claimed this visitor's own latest request in the conversation, or nothing.
-- Security definer: a visitor cannot read app.profile_photos, and gets only this one photo.
create or replace function app.my_handoff_photo(conversation text)
returns table (content_type text, data bytea, updated_at timestamptz)
language sql stable security definer set search_path = app, pg_temp as
$$ select ph.content_type, ph.data, ph.updated_at
   from (select h.assigned_to, h.tenant_id, h.status from app.handoffs h
          where h.tenant_id = app.current_tenant()
            and h.session_id = app.current_session()
            and h.conversation_id = conversation
          order by h.created_at desc limit 1) h
   join app.profile_photos ph on ph.profile_id = h.assigned_to and ph.tenant_id = h.tenant_id
   where h.status = 'claimed' $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant select, insert, update, delete on app.profile_photos to admitai_app;
        grant execute on function app.my_handoff_photo(text) to admitai_app;
    end if;
end $$;
