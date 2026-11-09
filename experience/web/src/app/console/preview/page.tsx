"use client";

import { useMe } from "@/components/console/Shell";
import { PageHeader } from "@/components/console/ui";

// Sandbox: the real widget, as visitors see it, for this college's public key.
export default function Preview() {
  const me = useMe();
  return (
    <>
      <PageHeader
        title="Preview widget"
        sub="Exactly what a visitor sees on the college website. Settings are changed under Admin → College settings."
      />
      <div className="flex justify-center">
        <iframe
          title="Widget preview"
          src={`/widget?key=${encodeURIComponent(me.tenant.public_key)}&utm_source=console_preview&preview=1`}
          className="h-[640px] w-full max-w-[400px] rounded-2xl border border-line bg-surface shadow-xl"
          allow="microphone"
        />
      </div>
    </>
  );
}
