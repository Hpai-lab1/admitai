"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Widget } from "@/components/widget/Widget";
import type { TenantInfo } from "@/lib/widget-api";

function WidgetFromQuery() {
  const params = useSearchParams();
  const key = params.get("key") ?? "";
  const [override, setOverride] = useState<Partial<TenantInfo> | undefined>();

  // The staff console's "Preview" screen sends unsaved settings to show them live (same origin only).
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin === window.location.origin && e.data?.type === "admitai:preview")
        setOverride(e.data.config);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  if (!key) return <p className="p-6 text-sm">This widget needs a college key (?key=…).</p>;
  return <Widget publicKey={key} themeOverride={override} />;
}

export default function WidgetPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm">Loading…</p>}>
      <WidgetFromQuery />
    </Suspense>
  );
}
