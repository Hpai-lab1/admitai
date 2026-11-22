-- Vapi phone numbers (vapi.py): the Vapi phone number a call comes in on selects the college, as the
-- WhatsApp business number does. Only the number's Vapi id is kept, never a caller's number.
alter table chat.channels drop constraint if exists channels_channel_check;
alter table chat.channels add constraint channels_channel_check
    check (channel in ('whatsapp', 'vapi'));
