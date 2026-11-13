"""WhatsApp text channel (PRD F1.11, F1.12, F8.1 on WhatsApp, F3.4 callbacks).

Inbound: Meta's webhook (signature-verified), answered by the same engine as the web widget.
Outbound: plain text (no Markdown or tables), citations as short numbered sources, at most three
messages per answer, quick-reply buttons. The WhatsApp number is personal data from the first
message (PRD 5.4): Module B keeps only a salted hash; the number goes to Module C only after the
person has agreed to be contacted.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
import psycopg
from cryptography.fernet import Fernet, InvalidToken

from . import hours, store
from .auth import Caller
from .db import Jsonb, tx
from .engine import Deps, handle_turn
from .experience_client import Experience, ExperienceError

log = logging.getLogger(__name__)

MAX_MESSAGES = 3
MAX_CHARS = 1000
WINDOW = timedelta(
    hours=24
)  # customer-service window: free-form replies only inside it
CONSENT_TEXT_VERSION = "wa-2026-10-v1"

BUTTON_HELPFUL, BUTTON_NOT_HELPFUL, BUTTON_HUMAN = "fb:up", "fb:down", "human"

TEXT = {
    "en": {
        "ask_contact": "May our admissions team contact you on this WhatsApp number? Reply YES or NO.",
        "callback_ack": "Thank you. Someone from the admissions team will contact you on this number by {due}.",
        "declined": "No problem. You can keep asking me questions here.",
        "minor": "As you are under 18, a parent or guardian needs to agree before we contact you. Please ask "
        "them to message us on WhatsApp from their own number.",
        "thanks": "Thank you for the feedback.",
        "error": "Sorry, I could not arrange that right now. Please try again in a little while.",
        "sources": "Sources",
        "human": "Talk to a person",
        "helpful": "Helpful",
        "not_helpful": "Not helpful",
        "erased": "Your messages and contact details with us are being deleted. If you write again, "
        "we start a new conversation.",
        "stopped": "You will not get WhatsApp messages from us any more. Write to us any time to start "
        "again. To delete your data, write DELETE MY DATA.",
        "guardian_code": "Hello. Your child asked {college} about admissions and gave this number so that "
        "you can agree to the college contacting you. Your code is {code}. Share it with them only if "
        "you agree. It is valid until {until}.",
    },
    "hi": {
        "ask_contact": "क्या हमारी प्रवेश टीम इस WhatsApp नंबर पर आपसे संपर्क कर सकती है? हाँ या नहीं लिखें।",
        "callback_ack": "धन्यवाद। प्रवेश टीम {due} तक इस नंबर पर आपसे संपर्क करेगी।",
        "declined": "कोई बात नहीं। आप यहाँ प्रश्न पूछते रहें।",
        "minor": "आपकी उम्र 18 से कम है, इसलिए संपर्क से पहले माता-पिता या अभिभावक की सहमति ज़रूरी है। कृपया उनसे "
        "अपने नंबर से हमें WhatsApp करने को कहें।",
        "thanks": "प्रतिक्रिया के लिए धन्यवाद।",
        "error": "माफ़ कीजिए, अभी यह नहीं हो सका। थोड़ी देर बाद फिर कोशिश करें।",
        "sources": "स्रोत",
        "human": "किसी से बात करें",
        "helpful": "उपयोगी",
        "not_helpful": "उपयोगी नहीं",
        "erased": "आपके संदेश और संपर्क विवरण हमारे यहाँ से हटाए जा रहे हैं। दोबारा लिखने पर नई बातचीत शुरू होगी।",
        "stopped": "अब आपको हमारी ओर से WhatsApp संदेश नहीं मिलेंगे। फिर से शुरू करने के लिए कभी भी लिखें। "
        "अपना डेटा हटाने के लिए लिखें: मेरा डेटा हटाएँ",
        "guardian_code": "नमस्ते। आपके बच्चे ने {college} में प्रवेश के बारे में पूछा है और यह नंबर दिया है ताकि आप "
        "कॉलेज को संपर्क करने की अनुमति दे सकें। आपका कोड {code} है। सहमति हो तभी यह कोड उन्हें बताएँ। "
        "यह {until} तक मान्य है।",
    },
    # Kannada written without a native speaker: have it reviewed before real use.
    "kn": {
        "ask_contact": "ನಮ್ಮ ಪ್ರವೇಶ ತಂಡ ಈ ವಾಟ್ಸ್‌ಆ್ಯಪ್ ಸಂಖ್ಯೆಯಲ್ಲಿ ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸಬಹುದೆ? ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಉತ್ತರಿಸಿ.",
        "callback_ack": "ಧನ್ಯವಾದಗಳು. ಪ್ರವೇಶ ತಂಡ {due} ಒಳಗೆ ಈ ಸಂಖ್ಯೆಯಲ್ಲಿ ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತದೆ.",
        "declined": "ಪರವಾಗಿಲ್ಲ. ನೀವು ಇಲ್ಲಿ ಪ್ರಶ್ನೆಗಳನ್ನು ಕೇಳುತ್ತಲೇ ಇರಬಹುದು.",
        "minor": "ನಿಮಗೆ 18 ವರ್ಷಕ್ಕಿಂತ ಕಡಿಮೆ ವಯಸ್ಸಾಗಿರುವುದರಿಂದ, ನಾವು ಸಂಪರ್ಕಿಸುವ ಮೊದಲು ಪೋಷಕರು ಅಥವಾ ರಕ್ಷಕರ ಒಪ್ಪಿಗೆ ಬೇಕು. "
        "ದಯವಿಟ್ಟು ಅವರ ಸ್ವಂತ ಸಂಖ್ಯೆಯಿಂದ ನಮಗೆ ವಾಟ್ಸ್‌ಆ್ಯಪ್ ಮಾಡಲು ಹೇಳಿ.",
        "thanks": "ನಿಮ್ಮ ಪ್ರತಿಕ್ರಿಯೆಗೆ ಧನ್ಯವಾದಗಳು.",
        "error": "ಕ್ಷಮಿಸಿ, ಈಗ ಅದು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "sources": "ಮೂಲಗಳು",
        "human": "ವ್ಯಕ್ತಿಯೊಂದಿಗೆ ಮಾತನಾಡಿ",
        "helpful": "ಉಪಯುಕ್ತ",
        "not_helpful": "ಉಪಯುಕ್ತವಲ್ಲ",
        "erased": "ನಿಮ್ಮ ಸಂದೇಶಗಳು ಮತ್ತು ಸಂಪರ್ಕ ವಿವರಗಳನ್ನು ಅಳಿಸಲಾಗುತ್ತಿದೆ. ನೀವು ಮತ್ತೆ ಬರೆದರೆ ಹೊಸ ಸಂಭಾಷಣೆ ಆರಂಭವಾಗುತ್ತದೆ.",
        "stopped": "ಇನ್ನು ಮುಂದೆ ನಿಮಗೆ ನಮ್ಮಿಂದ ವಾಟ್ಸ್‌ಆ್ಯಪ್ ಸಂದೇಶಗಳು ಬರುವುದಿಲ್ಲ. ಮತ್ತೆ ಆರಂಭಿಸಲು ಯಾವಾಗ ಬೇಕಾದರೂ ಬರೆಯಿರಿ. "
        "ನಿಮ್ಮ ಡೇಟಾ ಅಳಿಸಲು ಬರೆಯಿರಿ: DELETE MY DATA",
        "guardian_code": "ನಮಸ್ಕಾರ. ನಿಮ್ಮ ಮಗು {college} ನಲ್ಲಿ ಪ್ರವೇಶದ ಬಗ್ಗೆ ಕೇಳಿದೆ ಮತ್ತು ಕಾಲೇಜು ಸಂಪರ್ಕಿಸಲು ನೀವು ಒಪ್ಪಿಗೆ ನೀಡಲು "
        "ಈ ಸಂಖ್ಯೆಯನ್ನು ನೀಡಿದೆ. ನಿಮ್ಮ ಕೋಡ್ {code}. ನೀವು ಒಪ್ಪಿದರೆ ಮಾತ್ರ ಈ ಕೋಡ್ ಅನ್ನು ಅವರಿಗೆ ತಿಳಿಸಿ. "
        "ಇದು {until} ವರೆಗೆ ಮಾನ್ಯವಾಗಿರುತ್ತದೆ.",
    },
}
YES = re.compile(
    r"^\s*(?:(yes|y|ok|okay|sure|haan|han|ha|हाँ|हां|जी)\b|(ಹೌದು|ಸರಿ)(?=$|[\s.!,]))", re.I
)
NO = re.compile(r"^\s*(?:(no|n|nahi|nahin|नहीं)\b|(ಇಲ್ಲ|ಬೇಡ)(?=$|[\s.!,]))", re.I)
# Opting out (F1.11): Meta's own keywords and plain words; it withdraws the WhatsApp opt-in.
STOP = re.compile(
    r"^\s*(stop|unsubscribe|stop messages|opt ?out|रोकें|बंद करें)\s*[.!]?\s*$", re.I
)
# Erasure on request (PRD 11.4) for people who only ever write on WhatsApp.
ERASE = re.compile(
    r"^\s*(delete|erase|remove) my (data|details|chat)\s*[.!]?\s*$|^\s*मेरा डेटा हटाएँ?\s*$",
    re.I,
)


def t(key: str, lang: str) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key) or TEXT["en"][key]


# ------------------------------------------------------------------ webhook plumbing


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Meta signs the raw body with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse(payload: dict) -> list[dict]:
    """Inbound user messages from a Cloud API webhook payload (status updates are ignored)."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            for m in value.get("messages", []):
                item = {
                    "id": m.get("id"),
                    "from": m.get("from"),
                    "phone_number_id": phone_number_id,
                    "type": m.get("type"),
                    "text": None,
                    "button": None,
                    "reaction": None,
                    "reacted_to": None,
                }
                if m.get("type") == "text":
                    item["text"] = m.get("text", {}).get("body", "")
                elif m.get("type") == "interactive":
                    reply = m.get("interactive", {}).get("button_reply") or {}
                    item["button"], item["text"] = reply.get("id"), reply.get("title")
                elif m.get("type") == "button":
                    item["button"], item["text"] = (
                        m.get("button", {}).get("payload"),
                        m.get("button", {}).get("text"),
                    )
                elif m.get("type") == "reaction":
                    item["reaction"] = m.get("reaction", {}).get("emoji")
                    item["reacted_to"] = m.get("reaction", {}).get("message_id")
                out.append(item)
    return out


def contact_hash(tenant: str, number: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{tenant}:{number}".encode()).hexdigest()


# ------------------------------------------------------------------ formatting (F1.12)


def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(
        r"^\s*\|.*\|\s*$", "", text, flags=re.M
    )  # tables are not allowed on WhatsApp
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_messages(
    text: str, limit: int = MAX_CHARS, most: int = MAX_MESSAGES
) -> list[str]:
    """At most `most` messages, breaking between sentences; anything beyond is cut with an ellipsis
    (voice and WhatsApp answers are short by design)."""
    # Split after sentence ends, but keep what separated them: a blank line before "Sources:"
    # must stay a blank line, not become a space ("... per year [1]. Sources: [1] ...").
    pieces = re.split(r"((?<=[.!?।])\s+)", text.strip())
    parts, cur = [], ""
    for i in range(0, len(pieces), 2):
        s = pieces[i]
        sep = pieces[i - 1] if i else ""
        sep = "\n\n" if "\n\n" in sep else "\n" if "\n" in sep else " "
        if cur and len(cur) + len(sep) + len(s) > limit:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur}{sep}{s}" if cur else s
    if cur:
        parts.append(cur)
    if len(parts) > most:
        parts = parts[: most - 1] + [
            " ".join(parts[most - 1 :])[: limit - 1].rstrip() + "…"
        ]
    return [p[:limit] for p in parts]


def format_answer(text: str, sources: list[dict], lang: str) -> list[str]:
    body = strip_markdown(text)
    if sources:
        refs = "\n".join(
            f"[{s['n']}] {s['title']}" + (f" {s['url']}" if s.get("url") else "")
            for s in sources[:5]
        )
        body = f"{body}\n\n{t('sources', lang)}:\n{refs}"
    return split_messages(body)


def buttons(lang: str, offer_human: bool, rate: bool) -> list[dict]:
    """Reply buttons (max 3, titles max 20 characters)."""
    out = []
    if rate:
        out += [
            {"id": BUTTON_HELPFUL, "title": t("helpful", lang)[:20]},
            {"id": BUTTON_NOT_HELPFUL, "title": t("not_helpful", lang)[:20]},
        ]
    if offer_human or rate:
        out.append({"id": BUTTON_HUMAN, "title": t("human", lang)[:20]})
    return out[:3]


# ------------------------------------------------------------------ sending


class Sender(Protocol):
    def send(
        self,
        tenant: str,
        to: str,
        contact: str,
        text: str,
        buttons: list[dict] | None = None,
    ) -> str: ...

    # Outside the 24-hour window only a pre-approved template may be sent (F1.11).
    def send_template(
        self,
        tenant: str,
        to: str,
        contact: str,
        name: str,
        language: str,
        params: list[str],
    ) -> str: ...


class OutboxSender:
    """No Cloud API credentials (local prototype, tests): store what would be sent, without the number."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def send(
        self,
        tenant: str,
        to: str,
        contact: str,
        text: str,
        buttons: list[dict] | None = None,
    ) -> str:
        payload = {
            "type": "interactive" if buttons else "text",
            "text": text,
            "buttons": buttons or [],
        }
        return self._store(tenant, contact, payload)

    def send_template(
        self,
        tenant: str,
        to: str,
        contact: str,
        name: str,
        language: str,
        params: list[str],
    ) -> str:
        payload = {
            "type": "template",
            "template": name,
            "language": language,
            "params": params,
        }
        return self._store(tenant, contact, payload)

    def _store(self, tenant: str, contact: str, payload: dict) -> str:
        with tx(self.database_url, tenant) as cur:
            cur.execute(
                "insert into chat.wa_outbox (tenant_id, contact_hash, payload) values (%s,%s,%s) returning id",
                (tenant, contact, Jsonb(payload)),
            )
            return f"local.{cur.fetchone()['id']}"


class CloudApiSender:
    """WhatsApp Business Platform (Cloud API). The number is used in flight only."""

    def __init__(self, graph_url: str, phone_number_id: str, token: str):
        self.url = f"{graph_url}/{phone_number_id}/messages"
        self.headers = {"Authorization": f"Bearer {token}"}

    def send(
        self,
        tenant: str,
        to: str,
        contact: str,
        text: str,
        buttons: list[dict] | None = None,
    ) -> str:
        if buttons:
            message = {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text},
                    "action": {
                        "buttons": [{"type": "reply", "reply": b} for b in buttons]
                    },
                },
            }
        else:
            message = {"type": "text", "text": {"body": text, "preview_url": False}}
        return self._post(to, message)

    def send_template(
        self,
        tenant: str,
        to: str,
        contact: str,
        name: str,
        language: str,
        params: list[str],
    ) -> str:
        message = {
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in params],
                    }
                ],
            },
        }
        return self._post(to, message)

    def _post(self, to: str, message: dict) -> str:
        r = httpx.post(
            self.url,
            headers=self.headers,
            timeout=15,
            json={"messaging_product": "whatsapp", "to": to, **message},
        )
        r.raise_for_status()
        return r.json()["messages"][0]["id"]


# ------------------------------------------------------------------ handling one inbound message


# ------------------------------------------------------------------ guardian codes (F5.3)


def digits(number: str) -> str:
    """'+1 555-0100 042' and Meta's '15550100042' are the same number: digits only."""
    return re.sub(r"\D", "", number or "")


def _guardian_key(salt: str, tenant: str, number: str) -> bytes:
    raw = hashlib.sha256(
        f"{salt}:guardian-code:{tenant}:{digits(number)}".encode()
    ).digest()
    return base64.urlsafe_b64encode(raw)


def queue_guardian_code(
    database_url: str,
    tenant: str,
    number: str,
    code: str,
    expires_at: datetime,
    language: str,
    salt: str,
) -> None:
    """Keep a guardian's code until the guardian writes in. It is sealed with a key derived from the
    guardian's number (not stored), so only a message from that number can open it."""
    sealed = Fernet(_guardian_key(salt, tenant, number)).encrypt(code.encode()).decode()
    with tx(database_url, tenant) as cur:
        cur.execute("delete from chat.guardian_codes where expires_at < now()")
        cur.execute(
            "insert into chat.guardian_codes (tenant_id, contact_hash, sealed, language, expires_at)"
            " values (%s,%s,%s,%s,%s)",
            (
                tenant,
                contact_hash(tenant, digits(number), salt),
                sealed,
                language,
                expires_at,
            ),
        )


def take_guardian_codes(
    database_url: str, tenant: str, number: str, salt: str
) -> list[dict]:
    """The codes waiting for this number (removed as they are taken), newest last."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "delete from chat.guardian_codes where contact_hash = %s"
            " returning sealed, language, expires_at",
            (contact_hash(tenant, digits(number), salt),),
        )
        rows = cur.fetchall()
    key, now, out = (
        Fernet(_guardian_key(salt, tenant, number)),
        datetime.now(timezone.utc),
        [],
    )
    for r in sorted(rows, key=lambda r: r["expires_at"]):
        if r["expires_at"] <= now:
            continue
        try:
            code = key.decrypt(r["sealed"].encode()).decode()
        except InvalidToken:
            continue
        out.append(
            {"code": code, "language": r["language"], "expires_at": r["expires_at"]}
        )
    return out


# ------------------------------------------------------------------ messages outside the window


class NotifyRefused(RuntimeError):
    pass


def notify(
    deps: Deps,
    sender: Sender,
    tenant: str,
    number: str,
    kind: str,
    text: str,
    salt: str,
    now: datetime | None = None,
) -> dict:
    """A message Module C asks to send (a staff reply, a callback acknowledgement or reminder; PRD
    F3.4, F3.6). Inside the 24-hour window it is plain text; outside it only the college's
    pre-approved utility template for that kind of message may be used (F1.11). Never to someone
    who opted out."""
    s = deps.settings
    now = now or datetime.now(timezone.utc)
    contact = contact_hash(tenant, digits(number), salt)
    conv = f"wa-{contact[:24]}"
    with tx(s.database_url, tenant) as cur:
        cur.execute("select state from chat.conversations where id = %s", (conv,))
        row = cur.fetchone()
    state = (row or {}).get("state") or {}
    if state.get("opted_out"):
        raise NotifyRefused("This person asked not to get WhatsApp messages")
    cfg = store.tenant_config(s.database_url, tenant)
    if window_open(state, now):
        wa_id, mode = (
            sender.send(
                tenant, digits(number), contact, strip_markdown(text)[:MAX_CHARS]
            ),
            "text",
        )
    else:
        tpl = ((cfg.get("whatsapp") or {}).get("templates") or {}).get(kind)
        if not tpl or not tpl.get("name"):
            raise NotifyRefused(
                "Outside WhatsApp's 24-hour window only an approved template may be sent, and this "
                f"college has none for '{kind}'"
            )
        params = [cfg["college"], strip_markdown(text)[:1000]][
            : int(tpl.get("params", 2))
        ]
        wa_id = sender.send_template(
            tenant,
            digits(number),
            contact,
            tpl["name"],
            tpl.get("language", "en"),
            params,
        )
        mode = "template"
    with tx(s.database_url, tenant) as cur:
        cur.execute(
            "insert into chat.wa_messages (tenant_id, wa_message_id, direction, conversation_id)"
            " values (%s,%s,'out',%s) on conflict do nothing",
            (tenant, wa_id, conv),
        )
    return {"sent": mode, "wa_message_id": wa_id}


def tenant_for(database_url: str, phone_number_id: str) -> str | None:
    import psycopg

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select chat.tenant_for_channel('whatsapp', %s)", (phone_number_id,)
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _seen(cur, tenant: str, wa_id: str, conv: str) -> bool:
    cur.execute(
        "insert into chat.wa_messages (tenant_id, wa_message_id, direction, conversation_id) values (%s,%s,'in',%s)"
        " on conflict do nothing",
        (tenant, wa_id, conv),
    )
    return cur.rowcount == 0


def _seen_before(database_url: str, tenant: str, wa_id: str, conv: str) -> bool:
    with tx(database_url, tenant) as cur:
        return _seen(cur, tenant, wa_id, conv)


def _reacted_message(database_url: str, tenant: str, wa_id: str | None) -> str | None:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select message_id from chat.wa_messages where wa_message_id = %s and direction = 'out'",
            (wa_id,),
        )
        row = cur.fetchone()
    return str(row["message_id"]) if row and row["message_id"] else None


def _blocking(fn, *args, **kwargs):
    """Run a synchronous step (database, Module C, the Cloud API) in a worker thread, so one slow
    call never stalls the other conversations on this process."""
    return asyncio.to_thread(fn, *args, **kwargs)


# One person's messages are handled one at a time, in the order they arrived: each message reads
# the conversation's state and writes it back, so two at once would overwrite each other (a STOP
# sent while the previous answer is still being written would be lost). Two locks:
# - asyncio.Lock per person in this process: fair, so waiting messages go in arrival order;
# - a PostgreSQL advisory lock per person across processes and servers (several workers), held on
#   its own short-lived connection so a long answer does not hold a pooled one. PostgreSQL queues
#   its waiters in order too.
_locks: dict[str, asyncio.Lock] = {}
_lock_users: dict[str, int] = {}
# A worker that holds a person's lock for longer than this is assumed stuck: the message is then
# handled anyway (and logged) rather than lost.
SHARED_LOCK_WAIT_S = 60


def person_lock_key(key: str) -> int:
    """64-bit advisory-lock key for one person at one college (namespaced to WhatsApp)."""
    digest = hashlib.sha256(f"chat.whatsapp:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_shared_lock(database_url: str, key: int):
    try:
        conn = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as exc:  # no database: the in-process lock still holds
        log.warning(
            "whatsapp: shared lock unavailable (%s); in-process order only", exc
        )
        return None
    try:
        conn.execute(f"set lock_timeout = '{int(SHARED_LOCK_WAIT_S)}s'")
        conn.execute("select pg_advisory_lock(%s)", (key,))
        return conn
    except psycopg.errors.LockNotAvailable:
        log.warning(
            "whatsapp: another worker held a person's lock for %ss; going ahead",
            SHARED_LOCK_WAIT_S,
        )
        conn.close()
        return None
    except psycopg.Error as exc:
        log.warning("whatsapp: shared lock failed (%s); in-process order only", exc)
        conn.close()
        return None


def _drop_shared_lock(conn, key: int) -> None:
    try:
        conn.execute("select pg_advisory_unlock(%s)", (key,))
    except psycopg.Error:
        pass  # closing the connection releases it anyway
    finally:
        conn.close()


async def handle(
    deps: Deps,
    experience: Experience,
    sender: Sender,
    tenant: str,
    item: dict,
    salt: str,
    now: datetime | None = None,
) -> dict:
    """Process one inbound WhatsApp message end to end. Returns a small report (for tests/logs)."""
    key = f"{tenant}:{contact_hash(tenant, item['from'], salt)}"
    lock = _locks.setdefault(key, asyncio.Lock())
    _lock_users[key] = _lock_users.get(key, 0) + 1
    try:
        async with lock:
            shared_key = person_lock_key(key)
            held = (
                await _blocking(
                    _take_shared_lock, deps.settings.database_url, shared_key
                )
                if deps.settings.database_url
                else None
            )
            try:
                return await _handle(deps, experience, sender, tenant, item, salt, now)
            finally:
                if held is not None:
                    await _blocking(_drop_shared_lock, held, shared_key)
    finally:
        _lock_users[key] -= 1
        if not _lock_users[key]:
            del _lock_users[key], _locks[key]


async def _handle(
    deps: Deps,
    experience: Experience,
    sender: Sender,
    tenant: str,
    item: dict,
    salt: str,
    now: datetime | None,
) -> dict:
    s = deps.settings
    now = now or datetime.now(timezone.utc)
    contact = contact_hash(tenant, item["from"], salt)
    conv = f"wa-{contact[:24]}"
    if await _blocking(_seen_before, s.database_url, tenant, item["id"], conv):
        return {"duplicate": True}
    conv_row = await _blocking(
        store.open_conversation, s.database_url, tenant, conv, None, "whatsapp"
    )
    state = dict(conv_row["state"] or {})
    lang = conv_row["language"] or "en"
    first = "c_session" not in state

    def _send_now(text: str, btns: list[dict] | None, message_id: str | None) -> None:
        wa_id = sender.send(tenant, item["from"], contact, text, btns)
        with tx(s.database_url, tenant) as cur:
            cur.execute(
                "insert into chat.wa_messages (tenant_id, wa_message_id, direction, conversation_id, message_id)"
                " values (%s,%s,'out',%s,%s) on conflict do nothing",
                (tenant, wa_id, conv, message_id),
            )

    async def send(
        text: str, btns: list[dict] | None = None, message_id: str | None = None
    ) -> None:
        await _blocking(_send_now, text, btns, message_id)

    def _save_now() -> None:
        with tx(s.database_url, tenant) as cur:
            cur.execute(
                "update chat.conversations set state = %s, wa_contact_hash = %s where id = %s",
                (Jsonb(state), contact, conv),
            )

    async def save_state() -> None:
        await _blocking(_save_now)

    report: dict = {"conversation_id": conv}
    if first:
        # F1.11: opt-in recorded on the first message (the person wrote to the college).
        try:
            state["c_session"] = await _blocking(
                experience.open_session, tenant, "whatsapp"
            )
            await _blocking(
                experience.consent,
                tenant,
                state["c_session"],
                "whatsapp",
                CONSENT_TEXT_VERSION,
                "whatsapp",
            )
            report["opt_in_recorded"] = True
        except ExperienceError:
            report["opt_in_recorded"] = False
    state["last_inbound_at"] = now.isoformat()

    # ---------------------------------------------------------- opting out and back in (F1.11)
    if item.get("text") and STOP.match(item["text"]):
        try:
            await _blocking(
                experience.withdraw,
                tenant,
                state.get("c_session"),
                ["whatsapp", "marketing"],
            )
            report["opted_out"] = True
        except ExperienceError:
            report["opted_out"] = "not recorded in Module C"
        state["opted_out"] = True
        # A code waiting for this number is not sent after STOP.
        await _blocking(take_guardian_codes, s.database_url, tenant, item["from"], salt)
        await send(t("stopped", lang))  # the one reply Meta allows to an opt-out
        await save_state()
        return report
    if state.pop("opted_out", False) and state.get("c_session"):
        # They wrote again after opting out: writing to the college is a new opt-in.
        try:
            await _blocking(
                experience.consent,
                tenant,
                state["c_session"],
                "whatsapp",
                CONSENT_TEXT_VERSION,
                "whatsapp",
            )
            report["opt_in_recorded"] = True
        except ExperienceError:
            report["opt_in_recorded"] = False

    # ---------------------------------------------------------- a guardian's code (F5.3)
    codes = await _blocking(
        take_guardian_codes, s.database_url, tenant, item["from"], salt
    )
    if codes:
        latest = codes[-1]
        cfg = await _blocking(store.tenant_config, s.database_url, tenant)
        until = (
            latest["expires_at"]
            .astimezone(hours.zone(cfg.get("timezone")))
            .strftime("%H:%M")
        )
        await send(
            t("guardian_code", latest["language"]).format(
                college=cfg["college"], code=latest["code"], until=until
            )
        )
        report["guardian_code_sent"] = (
            True  # the code itself is never in a report or log
        )
        await save_state()
        return report

    # ---------------------------------------------------------- erasure on request (PRD 11.4)
    if item.get("text") and ERASE.match(item["text"]) and state.get("c_session"):
        try:
            # Module C calls back into this service to erase the transcripts.
            await _blocking(experience.erase, tenant, state["c_session"])
            report["erased"] = True
            await send(t("erased", lang))
        except ExperienceError:
            report["erased"] = False
            await send(t("error", lang))
            await save_state()
        return report  # the conversation's state is gone; do not write it back

    # ---------------------------------------------------------- feedback (F8.1 on WhatsApp)
    if item["type"] == "reaction" or item["button"] in (
        BUTTON_HELPFUL,
        BUTTON_NOT_HELPFUL,
    ):
        if item["type"] == "reaction":
            message_id = await _blocking(
                _reacted_message, s.database_url, tenant, item["reacted_to"]
            )
            value = (
                1
                if item["reaction"] in ("👍", "❤️", "🙏", "😊")
                else -1
                if item["reaction"] in ("👎", "😠")
                else 0
            )
        else:
            message_id = state.get("last_answer_id")
            value = 1 if item["button"] == BUTTON_HELPFUL else -1
        if message_id and value:
            try:
                await _blocking(
                    experience.feedback,
                    tenant,
                    state.get("c_session"),
                    {
                        "conversation_id": conv,
                        "message_id": message_id,
                        "kind": "thumb",
                        "value": value,
                        "channel": "whatsapp",
                        "trace_id": state.get("last_trace_id"),
                    },
                )
                report["feedback"] = value
            except ExperienceError:
                report["feedback"] = "failed"
            if item["type"] != "reaction":
                await send(t("thanks", lang))
        await save_state()
        return report

    # ---------------------------------------------------------- callback consent (F3.4, F5)
    if state.get("awaiting_contact_consent") and item.get("text"):
        if YES.match(item["text"]):
            state.pop("awaiting_contact_consent")
            report.update(
                await _blocking(
                    _callback, deps, experience, tenant, conv, state, item["from"], lang
                )
            )
            await send(report["reply"])
        elif NO.match(item["text"]):
            state.pop("awaiting_contact_consent")
            await send(t("declined", lang))
        else:
            state.pop(
                "awaiting_contact_consent"
            )  # a new question instead: answer it below
            return await _answer(
                deps, sender, send, save_state, tenant, conv, state, item, lang, report
            )
        await save_state()
        return report
    if item["button"] == BUTTON_HUMAN:
        report.update(await _ask_contact(state, lang, send))
        await save_state()
        return report
    return await _answer(
        deps, sender, send, save_state, tenant, conv, state, item, lang, report
    )


UNKNOWN_SESSION = "Unknown visitor session"


def _new_session(experience: Experience, tenant: str, state: dict) -> str:
    state["c_session"] = experience.open_session(tenant, "whatsapp")
    experience.consent(
        tenant, state["c_session"], "whatsapp", CONSENT_TEXT_VERSION, "whatsapp"
    )
    return state["c_session"]


async def _ask_contact(state: dict, lang: str, send) -> dict:
    if state.get("is_minor"):
        await send(t("minor", lang))
        return {"minor": True}
    state["awaiting_contact_consent"] = True
    await send(t("ask_contact", lang))
    return {"asked_contact": True}


def _callback(
    deps: Deps,
    experience: Experience,
    tenant: str,
    conv: str,
    state: dict,
    number: str,
    lang: str,
) -> dict:
    """The person said YES: contact consent, the lead (their own number) and a callback task with
    Module B's handoff package, all recorded in Module C."""
    session = state.get("c_session")
    view = store.conversation_view(deps.settings.database_url, tenant, conv) or {
        "messages": []
    }
    msgs = view["messages"]
    last = next((m for m in reversed(msgs) if m["sender"] == "assistant"), None)
    context = {
        "transcript": [
            {
                "role": m["sender"]
                if m["sender"] in ("user", "assistant")
                else "system",
                "text": m["content"][:4000],
            }
            for m in msgs[-40:]
        ],
        "ai_draft": last["content"][:4000] if last else None,
        "sources": [
            {
                "title": c["title"][:300],
                "section": c.get("heading_path"),
                "academic_year": c.get("academic_year"),
                "url": c.get("url"),
            }
            for c in (last or {}).get("sources", [])[:20]
        ],
        "trace_id": str(last["trace_id"]) if last and last.get("trace_id") else None,
        "confidence": last.get("confidence") if last else None,
    }
    handoff = state.get("last_handoff") or {
        "queue": "admission",
        "department": None,
        "reason": "Asked to talk to a person on WhatsApp",
    }

    def record(session: str) -> tuple[str, dict]:
        experience.consent(tenant, session, "contact", CONSENT_TEXT_VERSION, "whatsapp")
        lead_id = experience.lead(
            tenant,
            session,
            {
                "phone": "+" + number.lstrip("+"),
                "conversation_id": conv,
                "program_interest": state.get("programs", []),
            },
        )
        made = experience.handoff(
            tenant,
            session,
            {
                "conversation_id": conv,
                "session_id": session,
                "queue": handoff["queue"],
                "department": handoff.get("department"),
                "kind": "callback",
                "reason": handoff["reason"],
                "language": lang,
                "context": context,
                "preferred_channel": "whatsapp",
                "lead_id": lead_id,
            },
        )
        return lead_id, made

    try:
        try:
            lead_id, made = record(session)
        except ExperienceError as exc:
            if str(exc) != UNKNOWN_SESSION:
                raise
            # Module C no longer knows the session (erased or reset): start a new one and retry.
            lead_id, made = record(_new_session(experience, tenant, state))
    except ExperienceError as exc:
        return {"reply": t("error", lang), "callback_error": str(exc)}
    state["lead_created"] = True
    due = datetime.fromisoformat(made["sla_due_at"]).strftime("%d %b %Y, %H:%M UTC")
    return {"reply": t("callback_ack", lang).format(due=due), "callback": made["id"]}


async def _answer(
    deps, sender, send, save_state, tenant, conv, state, item, lang, report
) -> dict:
    text = item.get("text") or ""
    if not text.strip():
        await save_state()
        return report
    caller = Caller(tenant, state.get("c_session"), "service")
    reply, done, extra = "", {}, {}
    # The engine reads and writes the conversation itself; keep our channel state safe across it.
    await save_state()
    async for ev in handle_turn(deps, caller, conv, text, None, "whatsapp"):
        if ev["event"] == "token":
            reply += ev["data"]["text"]
        elif ev["event"] == "done":
            done = ev["data"]
        else:
            extra[ev["event"]] = ev["data"]
    row = await _blocking(_conversation_state, deps.settings.database_url, tenant, conv)
    merged = {
        **row["state"],
        **{k: v for k, v in state.items() if k not in row["state"]},
    }
    state.clear()
    state.update(merged)
    lang = row["language"] or lang
    if "consent_required" in extra:
        state["is_minor"] = True
    if "handoff_requested" in extra:
        state["last_handoff"] = extra["handoff_requested"]
    state["last_answer_id"], state["last_trace_id"] = (
        done.get("message_id"),
        done.get("trace_id"),
    )
    parts = format_answer(reply, done.get("sources", []), lang)
    rate = done.get("outcome") in ("answered", "partial")
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await send(
            part,
            buttons(lang, bool(done.get("offer_handoff")), rate) if last else None,
            done.get("message_id") if last else None,
        )
    await save_state()
    report.update({"outcome": done.get("outcome"), "messages": len(parts)})
    return report


def _conversation_state(database_url: str, tenant: str, conv: str) -> dict:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select state, language from chat.conversations where id = %s", (conv,)
        )
        return cur.fetchone()


def window_open(state: dict, now: datetime) -> bool:
    """F1.11: free-form messages only within 24 hours of the person's last message."""
    last = state.get("last_inbound_at")
    return bool(last) and now - datetime.fromisoformat(last) <= WINDOW


def dumps(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
