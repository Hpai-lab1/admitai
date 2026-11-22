"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import type { Strings } from "@/lib/i18n";
import { handoffPhoto, handoffStatus, type HandoffStatus } from "@/lib/widget-api";

const POLL_MS = 15000;

/** The words on the banner, or null when there is nothing to show (PRD 13). */
export function bannerText(s: HandoffStatus | null, t: Strings): string | null {
  if (!s || s.status === "none" || s.status === "resolved") return null;
  const team = s.department || t.admissions;
  return s.status === "claimed" && s.staff_first_name
    ? t.claimedBy(s.staff_first_name, team)
    : t.connecting(team);
}

/** "Connecting you to Admissions…", then the staff member's name once someone claims the request.
 *  A failed check keeps the last words on screen; it never tells the visitor something new. */
export function HandoffBanner({
  token,
  conversationId,
  t,
}: {
  token: string;
  conversationId: string;
  t: Strings;
}) {
  const [status, setStatus] = useState<HandoffStatus | null>(null);
  const finished = status?.status === "resolved";

  const [stopped, setStopped] = useState(false);

  useEffect(() => {
    if (finished || stopped) return;
    let live = true;
    const check = () => {
      if (document.hidden) return; // an unseen tab does not need to keep asking
      handoffStatus(token, conversationId)
        .then((s) => {
          if (live) setStatus(s);
        })
        .catch((e) => {
          // The session is over or refused, so asking again cannot help. A network failure can.
          if (live && e instanceof ApiError && e.status >= 400 && e.status < 500) setStopped(true);
        });
    };
    check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [token, conversationId, finished, stopped]);

  // The claimer's photo, fetched once per person who claims; the name is in the text, so the
  // image itself is decorative (alt="").
  const claimer = status?.status === "claimed" ? (status.staff_first_name ?? null) : null;
  const [photo, setPhoto] = useState<{ who: string; url: string | null } | null>(null);
  useEffect(() => {
    if (!claimer || photo?.who === claimer) return;
    let live = true;
    void handoffPhoto(token, conversationId).then((url) => {
      if (live) setPhoto({ who: claimer, url });
      else if (url) URL.revokeObjectURL(url);
    });
    return () => {
      live = false;
    };
  }, [claimer, photo?.who, token, conversationId]);
  useEffect(() => () => void (photo?.url && URL.revokeObjectURL(photo.url)), [photo?.url]);

  const text = bannerText(status, t);
  if (!text) return null;
  const src = claimer && photo?.who === claimer ? photo.url : null;
  return (
    <p
      role="status"
      className="flex items-center justify-center gap-2 bg-paper px-4 py-1.5 text-center text-xs font-medium text-ink"
    >
      {src && (
        // eslint-disable-next-line @next/next/no-img-element -- an object URL, not a static asset
        <img src={src} alt="" width={24} height={24} className="h-6 w-6 rounded-full object-cover" />
      )}
      {text}
    </p>
  );
}
