"""LiveKit room tokens (PRD F2.1, 9.3, 12.1 POST /voice/token: "a short-lived LiveKit room token").

A LiveKit access token is a JWT signed (HS256) with the server's API secret: `iss` is the API key,
`sub` the participant's identity, and `video` the grant (which room, may join, publish, subscribe).
Module B mints one per visitor and conversation; the room's metadata tells the voice worker which
college and conversation it serves, and cannot be changed by the browser (it is inside the signed
token).

What this does not include yet: the LiveKit server and the voice-agent worker that joins the room
(faster-whisper -> the same engine -> Piper). They need LiveKit on the lab servers (guide) and the
livekit-agents package; until then voice runs push-to-talk over HTTP (POST /voice/turn).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import jwt

TOKEN_MINUTES = 10


def room_name(tenant: str, conversation_id: str) -> str:
    """One room per conversation; the name reveals neither the college nor the conversation id."""
    return (
        "admitai-"
        + hashlib.sha256(f"{tenant}:{conversation_id}".encode()).hexdigest()[:24]
    )


def room_token(
    api_key: str,
    api_secret: str,
    room: str,
    identity: str,
    metadata: dict | None = None,
    minutes: int = TOKEN_MINUTES,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,  # the visitor's microphone
            "canSubscribe": True,  # the assistant's voice
            "canPublishData": True,  # captions and barge-in signals
        },
    }
    if metadata is not None:
        claims["metadata"] = json.dumps(metadata, separators=(",", ":"))
    return jwt.encode(claims, api_secret, algorithm="HS256")
