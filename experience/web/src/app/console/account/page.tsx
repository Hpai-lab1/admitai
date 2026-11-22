"use client";

import { useEffect, useState } from "react";
import { useMe } from "@/components/console/Shell";
import { Notice, PageHeader } from "@/components/console/ui";
import { ApiError, staff, staffImage } from "@/lib/api";
import { pushIsOn, pushSupported, turnPushOff, turnPushOn } from "@/lib/push";

/** Your account: the photo visitors see on the handoff banner once you claim their request. */
export default function Account() {
  const me = useMe();
  const [photo, setPhoto] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    staffImage("/auth/me/photo")
      .then((url) =>
        setPhoto((old) => {
          if (old) URL.revokeObjectURL(old);
          return url;
        }),
      )
      .catch(() => setPhoto(null));
  useEffect(() => {
    void load();
  }, []);

  const [push, setPush] = useState<boolean | null>(null);
  useEffect(() => {
    void pushIsOn()
      .then(setPush)
      .catch(() => setPush(false));
  }, []);
  async function togglePush() {
    setBusy(true);
    setMsg(null);
    try {
      if (push) {
        await turnPushOff();
        setPush(false);
        setMsg({ kind: "ok", text: "This browser will no longer notify you." });
      } else {
        await turnPushOn();
        setPush(true);
        setMsg({ kind: "ok", text: "This browser will notify you when something new is waiting." });
      }
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof Error ? e.message : "Could not change notifications" });
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setBusy(true);
    setMsg(null);
    try {
      const body = new FormData();
      body.append("file", file);
      await staff("/auth/me/photo", { method: "PUT", body });
      await load();
      setMsg({ kind: "ok", text: "Photo saved. Visitors see it after you claim their request." });
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "Upload failed" });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setMsg(null);
    try {
      await staff("/auth/me/photo", { method: "DELETE" });
      setPhoto(null);
      setMsg({ kind: "ok", text: "Photo removed. Visitors see your first name only." });
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "Could not remove the photo" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Your account" sub={me.full_name} />
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <section className="card max-w-xl p-5" aria-labelledby="photo-heading">
        <h2 id="photo-heading" className="text-base font-semibold">
          Photo on the visitor&apos;s chat
        </h2>
        <p className="mt-1 text-sm text-muted">
          When you claim a visitor&apos;s request, their chat says &quot;{me.full_name.split(" ")[0]} from the
          team has your request&quot;. A photo there makes it feel personal. It is optional: PNG, JPEG or
          WebP, at most 300 kB. Only the visitor whose request you claimed sees it.
        </p>
        <div className="mt-4 flex items-center gap-4">
          {photo ? (
            // eslint-disable-next-line @next/next/no-img-element -- an object URL, not a static asset
            <img
              src={photo}
              alt="Your photo"
              width={64}
              height={64}
              className="h-16 w-16 rounded-full object-cover"
            />
          ) : (
            <span
              aria-hidden
              className="grid h-16 w-16 place-items-center rounded-full bg-subtle text-xl font-bold text-muted"
            >
              {me.full_name.charAt(0)}
            </span>
          )}
          <label className="btn-quiet cursor-pointer">
            {photo ? "Replace photo" : "Add photo"}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="sr-only"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
                e.target.value = "";
              }}
            />
          </label>
          {photo && (
            <button className="btn-ghost" onClick={() => void remove()} disabled={busy}>
              Remove
            </button>
          )}
        </div>
      </section>
      <section className="card mt-4 max-w-xl p-5" aria-labelledby="push-heading">
        <h2 id="push-heading" className="text-base font-semibold">
          Browser notifications
        </h2>
        <p className="mt-1 text-sm text-muted">
          Get a notification on this computer or phone when a new callback or question is waiting for you,
          even when the console is closed. It only says that something is waiting: the details stay in the
          console.
        </p>
        {!me.channels?.push ? (
          <p className="mt-3 text-sm" role="status">
            Browser notifications are not set up on this server yet. Ask your administrator to add a push key.
          </p>
        ) : pushSupported() ? (
          <button
            className="btn-quiet mt-3"
            onClick={() => void togglePush()}
            disabled={busy || push === null}
          >
            {push ? "Turn off on this browser" : "Turn on for this browser"}
          </button>
        ) : (
          <p className="mt-3 text-sm" role="status">
            This browser does not support notifications. On an iPhone, add the console to the home screen
            first.
          </p>
        )}
      </section>
    </>
  );
}
