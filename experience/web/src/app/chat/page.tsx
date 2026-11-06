"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Widget } from "@/components/widget/Widget";

// Full-page chat (PRD F1.1): /chat?key=pk_... for a link in e-mails, posters and QR codes.
function FullPage() {
  const key = useSearchParams().get("key") ?? "";
  if (!key) return <p className="p-6 text-sm">This page needs a college key (?key=…).</p>;
  return <Widget publicKey={key} fullPage />;
}

export default function ChatPage() {
  return (
    <div className="min-h-dvh bg-paper">
      <Suspense fallback={<p className="p-6 text-sm">Loading…</p>}>
        <FullPage />
      </Suspense>
    </div>
  );
}
