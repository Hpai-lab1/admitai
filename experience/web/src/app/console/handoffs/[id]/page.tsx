"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CorrectionForm } from "@/components/console/CorrectionForm";
import { Badge, Loading, Notice, bandTone, useApi } from "@/components/console/ui";
import { useMe } from "@/components/console/Shell";
import { ApiError, staff } from "@/lib/api";
import { shortDate, slaText } from "@/lib/time";
import { type Handoff, replyEmail, replyWhatsapp } from "@/lib/types";

export default function HandoffDetail() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const { data: h, error, setData } = useApi<Handoff>(`/handoffs/${id}`);
  const [reply, setReply] = useState("");
  const [asCorrection, setAsCorrection] = useState(true);
  const [sendEmail, setSendEmail] = useState(false);
  const [sendWhatsapp, setSendWhatsapp] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [correcting, setCorrecting] = useState(false);

  async function act(path: string, body?: unknown, ok?: string) {
    setBusy(true);
    setMsg(null);
    try {
      await staff(`/handoffs/${id}/${path}`, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      });
      setData(await staff<Handoff>(`/handoffs/${id}`));
      if (ok) setMsg({ kind: "ok", text: ok });
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "That didn't work" });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Notice>{error}</Notice>;
  if (!h) return <Loading />;
  const sla = slaText(h.sla_due_at);
  const transcript = h.context.transcript ?? [];
  const lead = h.lead;
  const mineOrFree = !h.assigned_to || h.assigned_to === me.id;
  const emailTo = replyEmail(lead);
  const whatsappTo = replyWhatsapp(lead);

  return (
    <>
      <Link href="/console" className="text-sm font-semibold text-brand hover:underline">
        ← Back to queue
      </Link>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-extrabold text-accent">{h.reason}</h1>
        <Badge>{h.department ?? "Admissions"}</Badge>
        <Badge tone={h.status === "resolved" ? "green" : h.status === "claimed" ? "amber" : "brand"}>
          {h.status}
        </Badge>
        {h.status !== "resolved" && <Badge tone={sla.overdue ? "red" : "green"}>{sla.text}</Badge>}
      </div>
      {msg && (
        <div className="mt-4">
          <Notice kind={msg.kind}>{msg.text}</Notice>
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <section className="space-y-6 lg:col-span-2">
          <div className="card p-5">
            <h2 className="mb-3 font-bold">Conversation</h2>
            {transcript.length === 0 ? (
              <p className="text-sm text-muted">No transcript was attached.</p>
            ) : (
              <ol className="space-y-2">
                {transcript.map((t, i) => (
                  <li
                    key={i}
                    className={`rounded-lg px-3 py-2 text-sm ${t.role === "user" ? "bg-brand2/10" : "bg-paper"}`}
                  >
                    <span className="font-semibold">{t.role === "user" ? "Visitor" : "Assistant"}:</span>{" "}
                    {t.text}
                  </li>
                ))}
              </ol>
            )}
            {h.context.ai_draft && (
              <p className="mt-4 rounded-lg border border-dashed border-line px-3 py-2 text-sm">
                <span className="font-semibold">AI draft</span>
                {typeof h.context.confidence === "number" &&
                  ` (confidence ${Math.round(h.context.confidence * 100)}%)`}
                : {h.context.ai_draft}
              </p>
            )}
            {!!h.context.sources?.length && (
              <p className="mt-3 text-xs text-muted">
                <span className="font-semibold">Sources used:</span>{" "}
                {h.context.sources
                  .map(
                    (src, n) =>
                      `[${n + 1}] ${src.title}${src.academic_year ? ` (${src.academic_year})` : ""}`,
                  )
                  .join("  ")}
              </p>
            )}
            {h.context.ai_draft &&
              (correcting ? (
                <CorrectionForm
                  conversationId={h.conversation_id}
                  messageId={`handoff:${h.id}`}
                  originalAnswer={h.context.ai_draft}
                  onCancel={() => setCorrecting(false)}
                  onDone={(text) => {
                    setCorrecting(false);
                    setMsg({ kind: "ok", text });
                  }}
                />
              ) : (
                <button className="btn-secondary mt-3" onClick={() => setCorrecting(true)}>
                  Correct this answer
                </button>
              ))}
          </div>

          <div className="card p-5">
            <h2 className="mb-3 font-bold">Feedback in this conversation</h2>
            {h.feedback.length === 0 ? (
              <p className="text-sm text-muted">The visitor didn&apos;t rate any answers.</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {h.feedback.map((f, n) => (
                  <li key={n}>
                    <Badge tone={f.value < 0 || (f.kind === "rating" && f.value <= 2) ? "red" : "green"}>
                      {f.kind === "thumb" ? (f.value > 0 ? "👍" : "👎") : `★ ${f.value}/5`}
                      {f.reason ? ` ${f.reason.replace(/_/g, " ")}` : ""}
                    </Badge>
                    {f.comment && <span className="ml-1 text-xs text-muted">“{f.comment}”</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {h.status !== "resolved" && (
            <form
              className="card space-y-3 p-5"
              onSubmit={(e) => {
                e.preventDefault();
                void act(
                  "reply",
                  {
                    body: reply,
                    as_correction: asCorrection,
                    send_email: sendEmail && !!emailTo,
                    send_whatsapp: sendWhatsapp && !!whatsappTo,
                  },
                  [
                    "Reply saved",
                    sendEmail && emailTo ? `e-mailed to ${emailTo}` : "",
                    sendWhatsapp && whatsappTo ? `queued for WhatsApp to ${whatsappTo}` : "",
                    asCorrection ? "sent for review as a correction" : "",
                  ]
                    .filter(Boolean)
                    .join(", ") + ".",
                ).then(() => setReply(""));
              }}
            >
              <h2 className="font-bold">Your answer</h2>
              <label htmlFor="reply" className="sr-only">
                Your answer
              </label>
              <textarea
                id="reply"
                className="field min-h-32"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="Write the correct answer, or notes from your call…"
              />
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={asCorrection}
                  onChange={(e) => setAsCorrection(e.target.checked)}
                  className="mt-1 accent-[rgb(var(--brand))]"
                />
                <span>
                  Also suggest this as a correction, so the assistant answers it next time (goes to the
                  approval inbox).
                </span>
              </label>
              {emailTo && (
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={sendEmail}
                    onChange={(e) => setSendEmail(e.target.checked)}
                    className="mt-1 accent-[rgb(var(--brand))]"
                  />
                  <span>
                    E-mail this reply to {lead?.is_minor ? "the parent or guardian" : "the applicant"} (
                    {emailTo})
                  </span>
                </label>
              )}
              {whatsappTo && (
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={sendWhatsapp}
                    onChange={(e) => setSendWhatsapp(e.target.checked)}
                    className="mt-1 accent-[rgb(var(--brand))]"
                  />
                  <span>
                    Send this reply on WhatsApp to{" "}
                    {lead?.is_minor ? "the parent or guardian" : "the applicant"} ({whatsappTo}). Outside 24
                    hours of their last WhatsApp message only the college&apos;s approved template can be
                    sent; if it cannot go, this task says so.
                  </span>
                </label>
              )}
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" disabled={busy || !reply.trim() || !mineOrFree}>
                  Save reply
                </button>
                {h.status === "open" && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy}
                    onClick={() => void act("claim", undefined, "You own this task now.")}
                  >
                    Claim
                  </button>
                )}
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => void act("resolve", undefined, "Marked as done.")}
                >
                  Mark resolved
                </button>
              </div>
            </form>
          )}
        </section>

        <aside className="space-y-6">
          <div className="card p-5 text-sm">
            <h2 className="mb-3 font-bold">Contact</h2>
            {!lead ? (
              <p className="text-muted">
                No contact details (the visitor didn&apos;t share them, or withdrew consent).
              </p>
            ) : (
              <dl className="space-y-2">
                <div>
                  <dt className="text-xs text-muted">Name</dt>
                  <dd className="font-semibold">{lead.name ?? "—"}</dd>
                </div>
                {lead.is_minor ? (
                  <div>
                    <dt className="text-xs text-muted">Under 18: contact the parent or guardian</dt>
                    <dd className="font-semibold">
                      {lead.guardian_contact?.destination} ({lead.guardian_contact?.channel})
                    </dd>
                  </div>
                ) : (
                  <>
                    {lead.phone && (
                      <div>
                        <dt className="text-xs text-muted">Phone</dt>
                        <dd>
                          <a className="font-semibold text-brand" href={`tel:${lead.phone}`}>
                            {lead.phone}
                          </a>
                        </dd>
                      </div>
                    )}
                    {lead.email && (
                      <div>
                        <dt className="text-xs text-muted">E-mail</dt>
                        <dd>
                          <a className="font-semibold text-brand" href={`mailto:${lead.email}`}>
                            {lead.email}
                          </a>
                        </dd>
                      </div>
                    )}
                  </>
                )}
                <div>
                  <dt className="text-xs text-muted">Interested in</dt>
                  <dd>{lead.program_interest.join(", ") || "—"}</dd>
                </div>
                <div className="flex gap-2 pt-1">
                  <Badge tone={bandTone(lead.band)}>
                    {lead.band} · {lead.score}
                  </Badge>
                  <Badge>{lead.stage}</Badge>
                </div>
              </dl>
            )}
            {(h.preferred_channel || h.preferred_time) && (
              <p className="mt-3 border-t border-line pt-3 text-muted">
                Prefers {h.preferred_channel ?? "any channel"}
                {h.preferred_time ? `, ${h.preferred_time}` : ""}
              </p>
            )}
          </div>
          <div className="card p-5 text-sm">
            <h2 className="mb-3 font-bold">History</h2>
            <ol className="space-y-3 border-l-2 border-line pl-4">
              {h.events.map((e, i) => (
                <li key={i}>
                  <p className="font-semibold capitalize">
                    {e.kind}
                    {e.actor ? ` · ${e.actor}` : ""}
                  </p>
                  <p className="text-xs text-muted">{shortDate(e.at)}</p>
                  {e.body && <p className="mt-1 whitespace-pre-wrap">{e.body}</p>}
                </li>
              ))}
            </ol>
          </div>
        </aside>
      </div>
    </>
  );
}
