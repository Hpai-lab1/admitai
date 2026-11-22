"use client";

import { useEffect, useState } from "react";

import type { Strings } from "@/lib/i18n";

/** What the visitor sees while an answer has not started. Seen live (18 Nov 2026): a bare "…"
 * for 24.6 s when the answer model was slow, with no sign that anything was happening.
 * After 2 s it says what is happening, after 8 s that it is still working, and after 15 s it
 * offers a person (the same contact flow as "Talk to a person"). */
export const CHECKING_MS = 2000;
export const STILL_MS = 8000;
export const OFFER_MS = 15000;

export function Waiting({ t, onAskSomeone }: { t: Strings; onAskSomeone: () => void }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timers = [CHECKING_MS, STILL_MS, OFFER_MS].map((ms) => setTimeout(() => setElapsed(ms), ms));
    return () => timers.forEach(clearTimeout);
  }, []);
  if (elapsed < CHECKING_MS) return <span aria-hidden="true">…</span>;
  return (
    <span role="status">
      {elapsed < STILL_MS ? t.checking : t.stillChecking}
      {elapsed >= OFFER_MS && (
        <button type="button" className="btn-primary mt-2 block px-3 text-xs" onClick={onAskSomeone}>
          {t.askSomeone}
        </button>
      )}
    </span>
  );
}
