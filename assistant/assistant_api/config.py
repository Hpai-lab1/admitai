"""Settings from environment variables (see local.env.sh at the repository root)."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _dev() -> bool:
    return _env("APP_ENV", "production") == "development"


@dataclass(frozen=True)
class Settings:
    # The API connects as admitai_chat (RLS-restricted); migrations use the admin URL.
    database_url: str = field(default_factory=lambda: _env("CHAT_DATABASE_URL"))
    admin_database_url: str = field(
        default_factory=lambda: _env("CHAT_ADMIN_DATABASE_URL")
    )
    # Background jobs connect as the module's worker role (not superuser; BYPASSRLS, rights only in
    # this module's schemas). Unset: the admin connection, as before (migrations still use that).
    worker_database_url: str = field(
        default_factory=lambda: _env("CHAT_WORKER_DATABASE_URL", "")
        or _env("CHAT_ADMIN_DATABASE_URL", "")
    )
    knowledge_api_url: str = field(
        default_factory=lambda: _env("KNOWLEDGE_API_URL", "http://127.0.0.1:8100")
    )
    # Module A answers within its 350 ms budget on the lab GPU (PRD 7.5); a slow or missing Module A
    # costs the visitor at most this long before they get an honest "not sure" and a person.
    knowledge_timeout_s: float = field(
        default_factory=lambda: float(_env("KNOWLEDGE_TIMEOUT_S", "5"))
    )
    experience_api_url: str = field(
        default_factory=lambda: _env("EXPERIENCE_API_URL", "http://127.0.0.1:8200")
    )
    # Model gateway: any OpenAI-compatible endpoint (LiteLLM on the lab servers, Ollama locally).
    llm_base_url: str = field(
        default_factory=lambda: _env("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    )
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "local"))
    # Google AI Studio (Gemini) as an external answer model: used only for a college whose pack
    # opts in (model_policy.external: gemini), with personal details masked before sending; its
    # local model stays the fallback. OpenAI-compatible endpoint, so the same client is used.
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # Measured 13 Nov 2026 on a free AI Studio key: gemini-2.5-flash is closed to new keys;
    # 3.8-flash was often overloaded (HTTP 503); 3.5-flash answered in ~2 s but its free daily
    # quota ran out after a few dozen questions (HTTP 429); 3.5-flash-lite answered in ~1 s.
    # 17 Nov 2026, same RUAS prompt: 3.5-flash-lite had slowed to 8-9 s to the first word,
    # flash-lite-latest 2.1 s, 3.1-flash-lite 1.6 s; through the whole engine, five RUAS questions
    # went from a median 11.5 s to 3.8 s to the first words, all answered with sources. Speeds
    # change on Google's side: compare again with scripts/check_gemini.py before a demo.
    # A paid key can use GEMINI_MODEL=gemini-3.5-flash.
    gemini_model: str = field(
        default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.1-flash-lite")
    )
    # Gemini 3 models "think" before writing; with a short answer budget the thinking used it all
    # and the reply was empty. "low" keeps it short. gemini-3.1-flash-lite thinks least when no
    # setting is sent (empty: not sent): measured 17 Nov 2026, "low" spent 600-1,000 thinking
    # tokens on an answer prompt and the first word came after 3-7 s; "none" streamed nothing
    # for 12 s; gemini-3.5-flash-lite refuses "none" (HTTP 400). Other models keep "low".
    gemini_reasoning_effort: str = field(
        default_factory=lambda: _env(
            "GEMINI_REASONING_EFFORT",
            ""
            if _env("GEMINI_MODEL", "gemini-3.1-flash-lite").startswith(
                "gemini-3.1-flash-lite"
            )
            else "low",
        )
    )
    gemini_base_url: str = field(
        default_factory=lambda: _env(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
        )
    )
    # Gemini normally starts within 1.5-3 s; when Google is overloaded it may say nothing for
    # 20 s or more. Past this, the local model answers instead of the visitor waiting.
    gemini_first_token_s: float = field(
        default_factory=lambda: float(_env("GEMINI_FIRST_TOKEN_S", "6"))
    )
    # A one-shot Gemini call (translation, understanding, routing) with no reply after this many
    # seconds is sent again, and the first reply wins (clients.RoutingLLM._hedged): Google's
    # response time spikes now and then. 0 turns it off.
    gemini_hedge_s: float = field(
        default_factory=lambda: float(_env("GEMINI_HEDGE_S", "4"))
    )
    # The same for a streamed answer whose first word is late: measured 17 Nov 2026, the first
    # word usually comes after about 1.2 s and now and then after 3-10 s; of two requests sent
    # together the quicker started within 1.1-3.4 s. Each backup counts against the free daily
    # quota. 0 turns it off.
    gemini_stream_hedge_s: float = field(
        default_factory=lambda: float(_env("GEMINI_STREAM_HEDGE_S", "1.5"))
    )
    # PRD 9.5: Qwen2.5-14B answers, Llama-3.1-8B routes and is the fallback. Locally both are
    # qwen2.5:3b until the lab models are reachable; only these variables change.
    answer_model: str = field(
        default_factory=lambda: _env("ANSWER_MODEL", "qwen2.5:3b")
    )
    fallback_model: str = field(
        default_factory=lambda: _env("FALLBACK_MODEL", "qwen2.5:3b")
    )
    router_model: str = field(
        default_factory=lambda: _env("ROUTER_MODEL", "qwen2.5:3b")
    )
    # PRD 9.5 model policy: the model names a Tenant Pack may use, mapped to the gateway's own
    # names. Only these can ever be called; there are no external models in Level 1. On the lab
    # servers map them to the LiteLLM names; locally every name is the one small model.
    model_aliases: str = field(
        default_factory=lambda: _env(
            "MODEL_ALIASES",
            "local/qwen2.5-14b-instruct=qwen2.5:3b,local/llama3.1-8b=qwen2.5:3b,"
            "local/qwen2.5-3b=qwen2.5:3b",
        )
    )
    # PRD F9.4 pricing evidence: what 1,000 tokens cost, per gateway model name, in INR, as
    # "model=input:output,..." (for example "qwen2.5:3b=0.02:0.06"). Local models have no bill per
    # token, so this is the platform's own figure from GPU time; empty means no cost is shown.
    token_prices_inr: str = field(default_factory=lambda: _env("TOKEN_PRICES_INR"))
    # Languages whose visitors get the checked English answer translated by the model, kept only
    # when every number and date survives the translation (engine._translated). Off by default:
    # on the laptop's 3B model, Kannada translations kept the numbers but lost the meaning
    # (9 Nov 2026). Set TRANSLATED_ANSWER_LANGUAGES=kn on a model that passes a Kannada test.
    translated_answer_languages: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip()
            for x in _env("TRANSLATED_ANSWER_LANGUAGES", "").split(",")
            if x.strip()
        )
    )
    # Languages whose visitors are answered only in their own language: what cannot be answered
    # in it (catalogue templates, a native or a translated answer) is handed to a person, with a
    # message in the language, instead of an English answer (requested 9 Nov 2026 for Kannada).
    same_language_only: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip()
            for x in _env("SAME_LANGUAGE_ONLY", "hi,kn").split(",")
            if x.strip()
        )
    )
    # How long Ollama keeps the model loaded after a request (its default is 5 minutes). After a
    # pause, the first answer waited 8.3 s for the model to load again. Empty: not sent (LiteLLM).
    model_keep_alive: str = field(
        default_factory=lambda: _env("MODEL_KEEP_ALIVE", "30m")
    )
    # Plain catalogue fee/duration questions answered without the model (direct.py). 0 turns it
    # off: every question goes to the model again.
    direct_catalogue_answers: bool = field(
        default_factory=lambda: _env("DIRECT_CATALOGUE_ANSWERS", "1") == "1"
    )
    # An approved curated answer is given word for word, without the model, when the top search
    # result is that answer with a cross-encoder score of at least DIRECT_CURATED_MIN_RERANK
    # (direct.curated_answer; measured 12/12 right at 4.0 on RUAS). English replies; 0 turns it off.
    direct_curated_answers: bool = field(
        default_factory=lambda: _env("DIRECT_CURATED_ANSWERS", "1") == "1"
    )
    direct_curated_min_rerank: float = field(
        default_factory=lambda: float(_env("DIRECT_CURATED_MIN_RERANK", "4.0"))
    )
    use_model_router: bool = field(
        default_factory=lambda: _env("USE_MODEL_ROUTER", "1") == "1"
    )
    # Languages the answer model writes reliably. Measured locally: qwen2.5:3b writes garbled Hindi
    # (the sentence checks blocked it), so other languages get an English answer with a note.
    # With Qwen2.5-14B on the lab servers set NATIVE_ANSWER_LANGUAGES="en,hi" (PRD F1.6).
    native_answer_languages: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip()
            for x in _env("NATIVE_ANSWER_LANGUAGES", "en").split(",")
            if x.strip()
        )
    )
    llm_timeout_s: float = 20.0  # PRD F1 error handling
    max_output_tokens: int = 450
    # "development" allows the shared-secret set-up; anything else is production (safe default).
    app_env: str = field(default_factory=lambda: _env("APP_ENV", "production"))
    # Visitor tokens are issued by Module C. Production: verified with C's public key (RS256), so
    # this module can never mint a token. Development and tests: the shared HS256 secret.
    jwt_public_key_file: str = field(
        default_factory=lambda: _env("JWT_PUBLIC_KEY_FILE")
    )
    jwt_secret: str = field(
        default_factory=lambda: _env("JWT_SECRET") if _dev() else ""
    )
    # Voice tokens are this module's own, signed with its own secret.
    voice_token_secret: str = field(
        default_factory=lambda: (
            _env("VOICE_TOKEN_SECRET") or (_env("JWT_SECRET") if _dev() else "")
        )
    )
    # Inbound keys, one per caller: operator tooling, Module C, Module A.
    service_key: str = field(
        default_factory=lambda: (
            _env("KEY_OPS") or (_env("SERVICE_KEY") if _dev() else "")
        )
    )
    key_from_experience: str = field(default_factory=lambda: _env("KEY_C_TO_B"))
    key_from_knowledge: str = field(default_factory=lambda: _env("KEY_A_TO_B"))
    # Outbound keys this module presents.
    key_to_knowledge: str = field(
        default_factory=lambda: (
            _env("KEY_B_TO_A") or (_env("SERVICE_KEY") if _dev() else "")
        )
    )
    key_to_experience: str = field(
        default_factory=lambda: (
            _env("KEY_B_TO_C") or (_env("SERVICE_KEY") if _dev() else "")
        )
    )
    web_origin: str = field(
        default_factory=lambda: _env("WEB_ORIGIN", "http://127.0.0.1:8210")
    )
    # WhatsApp Business Platform (Cloud API). Without a token, replies go to chat.wa_outbox.
    whatsapp_verify_token: str = field(
        default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN")
    )
    whatsapp_app_secret: str = field(
        default_factory=lambda: _env("WHATSAPP_APP_SECRET")
    )
    whatsapp_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    whatsapp_graph_url: str = field(
        default_factory=lambda: _env(
            "WHATSAPP_GRAPH_URL", "https://graph.facebook.com/v21.0"
        )
    )
    # Salt for hashing WhatsApp numbers (PRD 11.2: the number is stored only in Module C).
    contact_salt: str = field(default_factory=lambda: _env("CONTACT_SALT"))
    # Voice (F2): local faster-whisper and Piper models (downloaded once into this folder).
    voice_model_dir: str = field(
        default_factory=lambda: _env("VOICE_MODEL_DIR", ".local/models")
    )
    whisper_size: str = field(default_factory=lambda: _env("WHISPER_SIZE", "base"))
    # Hindi/Kannada speech (chosen by the visitor, or detected): `base` is not usable for them
    # (benchmarks/stt-2026-11-13.json), `small` is. Set equal to WHISPER_SIZE for one model only.
    whisper_size_indic: str = field(
        default_factory=lambda: _env("WHISPER_SIZE_INDIC", "small")
    )
    # Load the speech models when the service starts, not on the first spoken question (which
    # then waited about 13 s). Off by default; the demo turns it on.
    voice_preload: bool = field(
        default_factory=lambda: _env("VOICE_PRELOAD", "0") == "1"
    )
    piper_voice: str = field(
        default_factory=lambda: _env("PIPER_VOICE", "en_US-lessac-medium")
    )
    # LiveKit (PRD F2.1, 12.1 POST /voice/token): when set, /voice/token also returns a room
    # token for the lab's LiveKit server; push-to-talk over HTTP keeps working either way.
    livekit_url: str = field(default_factory=lambda: _env("LIVEKIT_URL"))
    livekit_api_key: str = field(default_factory=lambda: _env("LIVEKIT_API_KEY"))
    livekit_api_secret: str = field(default_factory=lambda: _env("LIVEKIT_API_SECRET"))
    # Retell AI (retell.py): when set, /voice/token also returns a Retell web-call token; Retell
    # does speech-to-text and text-to-speech and asks this service for every answer.
    retell_api_key: str = field(default_factory=lambda: _env("RETELL_API_KEY"))
    retell_agent_id: str = field(default_factory=lambda: _env("RETELL_AGENT_ID"))
    # Vapi (vapi.py): phone calls, in and out, in English. Vapi does the telephony, hearing and
    # speaking with the college's saved Vapi assistant (its voice, greeting, transcriber); with
    # VAPI_ANSWERS=engine (default) every answer comes from this engine through Vapi's custom-LLM
    # hook, swapped in per call so the saved assistant is never changed. Retell is separate.
    vapi_api_key: str = field(default_factory=lambda: _env("VAPI_API_KEY"))
    vapi_assistant_id: str = field(default_factory=lambda: _env("VAPI_ASSISTANT_ID"))
    # The Vapi phone number (its id) that places outbound calls; inbound numbers are mapped to
    # colleges with `python -m assistant_api.vapi register <phone-number-id> --tenant-id <id>`.
    vapi_phone_number_id: str = field(
        default_factory=lambda: _env("VAPI_PHONE_NUMBER_ID")
    )
    # Public https address of this service, which Vapi calls back (a tunnel locally).
    vapi_public_url: str = field(default_factory=lambda: _env("VAPI_PUBLIC_URL"))
    # Shared secret Vapi sends back (X-Vapi-Secret / Bearer) on the webhook and custom-LLM calls.
    vapi_server_secret: str = field(default_factory=lambda: _env("VAPI_SERVER_SECRET"))
    # engine: answers from this service (checked, cited). assistant: the saved Vapi assistant's
    # own model answers, as configured in the Vapi dashboard (nothing of the college's sources).
    vapi_answers: str = field(
        default_factory=lambda: _env("VAPI_ANSWERS", "engine").lower()
    )
    # The college for an inbound call on a number not registered to one (a one-college set-up).
    vapi_default_tenant: str = field(default_factory=lambda: _env("VAPI_TENANT_ID"))
    # Sarvam AI (sarvam.py): Indian-language speech for push-to-talk and WhatsApp voice notes
    # (Saaras speech-to-text, Bulbul text-to-speech). Unset: everything stays on the local
    # Whisper and Piper models, and Hindi and Kannada replies are text only, as before.
    sarvam_api_key: str = field(default_factory=lambda: _env("SARVAM_API_KEY"))
    sarvam_base_url: str = field(
        default_factory=lambda: _env("SARVAM_BASE_URL", "https://api.sarvam.ai")
    )
    sarvam_stt_model: str = field(
        default_factory=lambda: _env("SARVAM_STT_MODEL", "saaras:v4")
    )
    # codemix (default): English words kept in English letters ("B.Tech CSE fees ಎಷ್ಟು?"), so
    # programme names and exam names match the catalogue and the search; transcribe wrote them in
    # Kannada or Devanagari ("ಬಿಟೆಕ್ ಸಿಎಸ್ಇ", COMEDK as "ಕಾಮೆಂಟ್ಕೆ"; measured 16 Nov 2026).
    # Also: transcribe (all native script), verbatim.
    sarvam_stt_mode: str = field(
        default_factory=lambda: _env("SARVAM_STT_MODE", "codemix")
    )
    sarvam_tts_model: str = field(
        default_factory=lambda: _env("SARVAM_TTS_MODEL", "bulbul:v4-flash")
    )
    # One voice per language, "en=...,hi=...,kn=...": Bulbul v4 voices are per language.
    sarvam_speakers: str = field(
        default_factory=lambda: _env(
            "SARVAM_SPEAKERS",
            "en=simran_en_customer,hi=simran_hi_assistant,kn=chaitra_kn_conversation",
        )
    )
    sarvam_timeout_s: float = field(
        default_factory=lambda: float(_env("SARVAM_TIMEOUT_S", "15"))
    )
    sarvam_retries: int = field(
        default_factory=lambda: int(_env("SARVAM_RETRIES", "2"))
    )
    # Which engine hears and which speaks: local (Whisper/Piper only), sarvam, or auto (the
    # default: Sarvam hears every recording when it has a key, so Hindi, Kannada and code-mixed
    # speech are recognised; English is still spoken by Piper, Hindi and Kannada by Sarvam).
    speech_to_text_provider: str = field(
        default_factory=lambda: _env("SPEECH_TO_TEXT_PROVIDER", "auto").lower()
    )
    text_to_speech_provider: str = field(
        default_factory=lambda: _env("TEXT_TO_SPEECH_PROVIDER", "auto").lower()
    )
    # A college that opted in to Gemini (model_policy.external: gemini) gets its checked English
    # answers translated into these languages by Gemini (engine._translated), so Hindi and
    # Kannada visitors get natural answers to every question, not only catalogue templates.
    gemini_translated_languages: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip()
            for x in _env("GEMINI_TRANSLATED_LANGUAGES", "hi,kn").split(",")
            if x.strip()
        )
    )
    # ...and its Hindi and Kannada questions are put into English first (one short Gemini call),
    # so routing, the search of its English sources and the sentence checks work as for English.
    # 0: the question is searched as written (cross-language search missed answers in testing).
    translate_questions: bool = field(
        default_factory=lambda: _env("TRANSLATE_QUESTIONS", "1") == "1"
    )
    # A WhatsApp voice note is answered with a voice note too (and the text), when a voice for
    # the reply language exists. 0: text replies only.
    whatsapp_voice_replies: bool = field(
        default_factory=lambda: _env("WHATSAPP_VOICE_REPLIES", "1") == "1"
    )
    rate_limit_per_minute: int = 12  # per visitor session, answered in the chat (F1)
    # PRD 12.3: also per IP and per tenant, answered with 429 and Retry-After. A whole school's
    # computer lab can share one IP, so the IP limit is generous; it stops floods, not visitors.
    rate_limit_per_ip_minute: int = field(
        default_factory=lambda: int(_env("RATE_LIMIT_PER_IP_MINUTE", "60"))
    )
    rate_limit_per_tenant_minute: int = field(
        default_factory=lambda: int(_env("RATE_LIMIT_PER_TENANT_MINUTE", "600"))
    )
    # Only behind a proxy you run (Cloudflare Tunnel) may X-Forwarded-For be believed; anywhere
    # else it is the caller's own claim and would let anyone dodge the IP limit.
    trust_proxy_headers: bool = field(
        default_factory=lambda: _env("TRUST_PROXY_HEADERS") == "1"
    )
    # PRD 11.4: transcript and audio retention sweep every this many hours, from the running
    # service. 0 (the code default, so test apps start no thread) turns it off; local.env.sh and
    # the server set 24. Seen 18 Nov 2026: the sweep existed but nothing ever ran it.
    retention_sweep_hours: float = field(
        default_factory=lambda: float(_env("RETENTION_SWEEP_HOURS", "0"))
    )
    history_turns: int = 10  # F1.3
    max_sources: int = 5


_LAB_SUFFIXES = (".local", ".internal", ".lan", ".svc", ".cluster.local", ".localhost")


def is_lab_address(url: str) -> bool:
    """True for a gateway on this machine or the lab's own network: loopback or private addresses,
    a single-label name (a Docker Compose service such as `litellm`), or an internal domain.
    Level 1 calls only the lab's models (PRD 9.5), so anything else is refused in production."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        pass
    return host == "localhost" or "." not in host or host.endswith(_LAB_SUFFIXES)


def check_production(s: Settings) -> None:
    """Refuse an unsafe production configuration loudly instead of running open."""
    if s.app_env == "development":
        return
    problems = []
    if not is_lab_address(s.llm_base_url):
        problems.append(
            "LLM_BASE_URL must be the lab's model gateway (LiteLLM or Ollama on the lab network): "
            "Level 1 makes no external model calls"
        )
    if bool(s.livekit_api_key) != bool(s.livekit_api_secret) or (
        s.livekit_api_key and not s.livekit_url
    ):
        problems.append(
            "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET go together"
        )
    if s.whatsapp_token or s.whatsapp_app_secret:
        if not s.whatsapp_app_secret:
            problems.append(
                "WHATSAPP_APP_SECRET is required to check Meta's signatures"
            )
        if len(s.contact_salt) < 16:
            problems.append(
                "CONTACT_SALT (16+ characters) is required: WhatsApp numbers are kept only as salted hashes"
            )
    if s.vapi_api_key:
        if s.vapi_answers not in ("engine", "assistant"):
            problems.append("VAPI_ANSWERS must be engine or assistant")
        if s.vapi_answers == "engine" and len(s.vapi_server_secret) < 24:
            problems.append(
                "VAPI_SERVER_SECRET (24+ characters) is required: it is how Vapi's calls to "
                "this service are told apart from anyone else's"
            )
    for name, value in (
        ("SPEECH_TO_TEXT_PROVIDER", s.speech_to_text_provider),
        ("TEXT_TO_SPEECH_PROVIDER", s.text_to_speech_provider),
    ):
        if value not in ("auto", "local", "sarvam"):
            problems.append(f"{name} must be auto, local or sarvam")
        elif value == "sarvam" and not s.sarvam_api_key:
            problems.append(f"{name}=sarvam needs SARVAM_API_KEY")
    if not s.jwt_public_key_file:
        problems.append("JWT_PUBLIC_KEY_FILE is required (Module C's public key)")
    if not s.voice_token_secret or len(s.voice_token_secret) < 32:
        problems.append("VOICE_TOKEN_SECRET (32+ characters) is required")
    if not (s.key_from_experience and s.key_from_knowledge):
        problems.append("KEY_C_TO_B and KEY_A_TO_B are required")
    for name in ("SERVICE_KEY", "JWT_SECRET"):
        if os.environ.get(name):
            problems.append(
                f"{name} is a development setting and must not be set in production"
            )
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
