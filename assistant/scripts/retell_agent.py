"""Create or update the Retell AI voice agent that answers through this service (retell.py).

    source local.env.sh
    python assistant/scripts/retell_agent.py --public-url https://<your-tunnel>.trycloudflare.com

- Without RETELL_AGENT_ID: creates a voice agent whose answers come from this service (Retell's
  "custom LLM" WebSocket at <public-url>/voice/retell/llm) and prints the line to add to
  local.secrets.sh.
- With RETELL_AGENT_ID: points that agent at the new public URL (a quick tunnel gets a new
  address every time it starts) and applies the language settings below.

Languages (--languages, default en,hi,kn): one call can be in English, Hindi or Kannada and can
switch between them at any turn; this service decides each reply's language (language.py) and
sends the text in that language. Per Retell's docs (checked 16 Nov 2026):
- `language` takes a list of locales for a multilingual agent (en-IN, hi-IN, kn-IN).
- Speech recognition for Kannada is offered only by Soniox and Azure; Soniox also handles
  code-switching ("B.Tech fees eshtu?"), so the agent uses custom STT with Soniox.
- The voice must speak every language, in one voice: Indian-accented "11labs-Monika" with
  eleven_v3 (eleven_multilingual_v2 and eleven_flash_v2_5 have no Kannada). "openai-Monika" with
  gpt-4o-mini-tts speaks them too, but its voice changed from sentence to sentence.
With --languages en the agent is English-only, as before (11labs-Monika, Retell's default STT).

Needs RETELL_API_KEY (Retell dashboard -> Settings -> API Keys). The key never leaves this machine
except in the call to Retell's own API.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

API = "https://api.retellai.com"
# Turn-taking that suited admissions questions in testing: wait a little longer before deciding
# the caller has finished, and interruptions allowed. No "mm-hmm"s while they talk: Retell says
# them over the caller, and visitors heard them as a second assistant talking (18 Nov 2026).
TUNING = {
    "responsiveness": 0.6,
    "enable_backchannel": False,
    "interruption_sensitivity": 0.8,
}
LOCALES = {"en": "en-IN", "hi": "hi-IN", "kn": "kn-IN"}
# Words speech recognition should keep as they are (Retell's boosted_keywords).
KEYWORDS = [
    "B.Tech",
    "M.Tech",
    "MBA",
    "BBA",
    "CSE",
    "KCET",
    "COMEDK",
    "JEE",
    "PUC",
    "hostel",
]
ENGLISH_VOICE = ("11labs-Monika", None)
# One voice for the whole call: OpenAI's gpt-4o-mini-tts made each sentence separately and the
# voice audibly changed between sentences (18 Nov 2026); ElevenLabs keeps the same Monika voice,
# and its v3 model speaks English, Hindi and Kannada.
MULTILINGUAL_VOICE = ("11labs-Monika", "eleven_v3")
# Soniox's end-of-speech wait (Retell allows 500-3,000 ms for it).
SONIOX_ENDPOINTING_MS = 600


def agent_body(
    websocket_url: str,
    languages: list[str],
    voice: str | None = None,
    voice_model: str | None = None,
    stt: str | None = None,
) -> dict:
    """The fields this service sets on the agent (no secrets)."""
    unknown = [x for x in languages if x not in LOCALES]
    if unknown or not languages:
        raise ValueError(f"languages must be among {', '.join(LOCALES)}")
    multilingual = len(languages) > 1 or languages != ["en"]
    body: dict = {
        "response_engine": {"type": "custom-llm", "llm_websocket_url": websocket_url},
        **TUNING,
        "boosted_keywords": KEYWORDS,
    }
    if not multilingual:
        body["language"] = LOCALES["en"]
        body["voice_id"] = voice or ENGLISH_VOICE[0]
        if voice_model:
            body["voice_model"] = voice_model
        return body
    default_voice, default_model = MULTILINGUAL_VOICE
    body["language"] = [LOCALES[x] for x in languages]
    body["voice_id"] = voice or default_voice
    if voice_model or (voice is None and default_model):
        body["voice_model"] = voice_model or default_model
    provider = stt or ("soniox" if "kn" in languages else None)
    if provider:
        body["stt_mode"] = "custom"
        body["custom_stt_config"] = {
            "provider": provider,
            "endpointing_ms": SONIOX_ENDPOINTING_MS,
        }
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--public-url",
        required=True,
        help="https address that reaches Module B (port 8150)",
    )
    ap.add_argument(
        "--languages",
        default="en,hi,kn",
        help="languages the call may use, comma-separated (en, hi, kn); en alone = English only",
    )
    ap.add_argument(
        "--voice", help="a Retell voice id (default: Monika, Indian English)"
    )
    ap.add_argument(
        "--voice-model", help="the voice's model, e.g. gpt-4o-mini-tts or eleven_v3"
    )
    ap.add_argument(
        "--stt",
        help="custom speech-to-text provider (default soniox when Kannada is on)",
    )
    ap.add_argument("--name", default="AdmitAI Admissions (voice)")
    a = ap.parse_args()
    key = os.environ.get("RETELL_API_KEY")
    if not key:
        sys.exit(
            "Set RETELL_API_KEY first (in local.secrets.sh, then source local.env.sh)"
        )
    base = a.public_url.rstrip("/")
    if not base.startswith("https://"):
        sys.exit(
            "--public-url must start with https:// (Retell connects from the internet)"
        )
    ws = "wss://" + base[len("https://") :] + "/voice/retell/llm"
    languages = [x.strip() for x in a.languages.split(",") if x.strip()]
    try:
        body = agent_body(ws, languages, a.voice, a.voice_model, a.stt)
    except ValueError as exc:
        sys.exit(str(exc))
    # Module B must answer through the tunnel before Retell is pointed at it. A brand-new tunnel
    # address can take a few seconds to resolve, so try a few times before giving up.
    last = ""
    for attempt in range(6):
        try:
            health = httpx.get(f"{base}/health", timeout=15)
            if health.status_code == 200 and health.json().get("module") == "assistant":
                break
            last = f"{base}/health does not reach Module B (got {health.status_code})"
        except httpx.HTTPError as exc:
            last = f"{base} is not reachable: {exc}"
        time.sleep(5)
    else:
        if "NEW-ADDRESS" in base:
            last += " (replace NEW-ADDRESS with the address cloudflared printed)"
        sys.exit(last)
    h = {"Authorization": f"Bearer {key}"}
    agent_id = os.environ.get("RETELL_AGENT_ID")
    if agent_id:
        r = httpx.patch(
            f"{API}/update-agent/{agent_id}", headers=h, json=body, timeout=30
        )
    else:
        body |= {"agent_name": a.name}
        r = httpx.post(f"{API}/create-agent", headers=h, json=body, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"Retell refused ({r.status_code}): {r.text[:300]}")
    out = r.json()
    langs = out.get("language")
    print(
        f"Agent {out['agent_id']} now answers through {ws}\n"
        f"  languages: {langs}; voice: {out.get('voice_id')} {out.get('voice_model') or ''}; "
        f"speech-to-text: {out.get('stt_mode') or 'default'}"
    )
    if not agent_id:
        print(
            "Add this line to local.secrets.sh, then restart Module B (scripts/run_local.sh):"
        )
        print(f'export RETELL_AGENT_ID="{out["agent_id"]}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
