"""Staff photos for the visitor's handoff banner (PRD 13: "Connecting you to Admissions…", then
the name and photo of whoever claims the request).

Staff upload, see and remove only their own photo (RLS). A visitor sees one photo: the person who
claimed their own request in this conversation, and only while it is claimed. The image type is
checked from the file's own bytes, not from what the browser says, and the size is capped.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from ..config import Settings
from ..db import Context, audit, tx
from ..deps import require_staff, settings_dep, visitor_context

router = APIRouter(tags=["handoffs"])
STAFF = require_staff()
MAX_BYTES = 300_000


def image_type(data: bytes) -> str | None:
    """PNG, JPEG or WebP by their signatures; anything else (SVG, HTML, ...) is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.put(
    "/auth/me/photo",
    status_code=204,
    summary="Add or replace your photo (PNG, JPEG or WebP, at most 300 kB)",
)
async def put_my_photo(
    file: UploadFile = File(...),
    ctx: Context = Depends(STAFF),
    settings: Settings = Depends(settings_dep),
):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The photo must be at most 300 kB"
        )
    kind = image_type(data)
    if kind is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Use a PNG, JPEG or WebP image"
        )
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.profile_photos (profile_id, tenant_id, content_type, data)"
            " values (%s, %s, %s, %s) on conflict (profile_id) do update"
            " set content_type = excluded.content_type, data = excluded.data, updated_at = now()",
            (ctx.profile_id, ctx.tenant_id, kind, data),
        )
        audit(cur, ctx, "profile.photo_set", "profile", ctx.profile_id)
    return Response(status_code=204)


@router.delete("/auth/me/photo", status_code=204, summary="Remove your photo")
def delete_my_photo(
    ctx: Context = Depends(STAFF), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "delete from app.profile_photos where profile_id = %s", (ctx.profile_id,)
        )
        if cur.rowcount:
            audit(cur, ctx, "profile.photo_removed", "profile", ctx.profile_id)
    return Response(status_code=204)


def _image(row) -> Response:
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No photo")
    return Response(
        content=bytes(row["data"]),
        media_type=row["content_type"],
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.get("/auth/me/photo", summary="Your photo, as the visitor would see it")
def get_my_photo(
    ctx: Context = Depends(STAFF), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select content_type, data from app.profile_photos where profile_id = %s",
            (ctx.profile_id,),
        )
        return _image(cur.fetchone())


@router.get(
    "/handoffs/mine/photo",
    summary="Photo of the staff member who claimed your request, if they added one (PRD 13)",
)
def my_handoff_photo(
    conversation_id: str = Query(min_length=1, max_length=200),
    ctx: Context = Depends(visitor_context),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select * from app.my_handoff_photo(%s)", (conversation_id,))
        return _image(cur.fetchone())
