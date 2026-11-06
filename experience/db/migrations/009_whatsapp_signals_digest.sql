-- Level 1 completion for Module C.
--
-- PRD F8.3: rephrase, talk-to-human, voice hang-up and barge-in events go to Module A's learning
-- loop as `signal.logged` (kind, conversation and message ids, channel; never visitor text).
alter table app.outbox_events drop constraint outbox_events_type_check;
alter table app.outbox_events add constraint outbox_events_type_check
    check (type in ('feedback.created', 'correction.submitted', 'erasure.requested', 'signal.logged'));

-- PRD F3.4, F3.6: callback acknowledgements, overdue updates and staff replies can also go out on
-- WhatsApp, through Module B (app.message_outbox.channel = 'whatsapp' already exists). A handoff
-- records that a reply was sent that way.
alter table app.handoff_events drop constraint handoff_events_kind_check;
alter table app.handoff_events add constraint handoff_events_kind_check
    check (kind in ('created', 'claimed', 'replied', 'resolved', 'reopened', 'escalated', 'emailed',
                    'messaged'));
