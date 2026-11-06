"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { STRINGS, type Lang, type Strings } from "@/lib/i18n";
import { visitor, type TenantInfo } from "@/lib/widget-api";

type Intent = "callback" | "enquiry";
type Step = "consent" | "age" | "adult" | "guardian" | "code" | "minor" | "done";
type Channel = "phone" | "email" | "whatsapp";
type Turn = { role: "user" | "assistant"; text: string };

type Props = {
  intent: Intent;
  token: string;
  tenant: TenantInfo;
  lang?: Lang;
  conversationId: string;
  transcript: Turn[];
  reason?: string;
  /** From Module B's handoff_requested event: which queue should call back. */
  queue?: "admission" | "faculty";
  department?: string;
  /** From Module B's lead_prompt event: program codes asked about and the rule-based score. */
  programs?: string[];
  score?: number;
  /** Module B detected an under-18 enquirer: skip the age question, go to the guardian. */
  knownMinor?: boolean;
  onClose: (message?: string) => void;
};

/** "Talk to a person" / "Enquire now": consent first (DPDP), then contact details. Under-18s give a
 *  parent's contact, confirmed by a one-time code; their own phone and e-mail are never asked for (PRD F5.3). */
export function ContactFlow({
  intent,
  token,
  tenant,
  lang = "en",
  conversationId,
  transcript,
  reason,
  queue,
  department,
  programs,
  score,
  knownMinor = false,
  onClose,
}: Props) {
  const t = STRINGS[lang];
  const [step, setStep] = useState<Step>("consent");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [marketing, setMarketing] = useState(false);
  const [contactOk, setContactOk] = useState(false);
  const [form, setForm] = useState({
    name: "",
    phone: "",
    email: "",
    program: programs?.join(", ") ?? "",
    preferred_time: "",
    channel: "phone" as Channel,
    guardianChannel: "email" as "email" | "whatsapp",
    guardianDest: "",
    code: "",
  });
  const [verification, setVerification] = useState<{
    id: string;
    devCode: string | null;
    message: string;
  } | null>(null);
  const [doneMessage, setDoneMessage] = useState("");
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const tv = tenant.consent_text_version;

  const agree = () =>
    run(async () => {
      await visitor(token, "/consents", { purpose: "contact", granted: true, text_version: tv });
      await visitor(token, "/consents", { purpose: "marketing", granted: marketing, text_version: tv });
      setStep(knownMinor ? "guardian" : "age");
    });

  async function finish(isMinor: boolean, preferredChannel: Channel) {
    await visitor(token, "/leads", {
      name: form.name || null,
      phone: isMinor ? null : form.phone || null,
      email: isMinor ? null : form.email || null,
      program_interest: form.program ? form.program.split(/,\s*/).filter(Boolean) : [],
      preferred_time: form.preferred_time || null,
      is_minor: isMinor,
      score: score ?? null,
      conversation_id: conversationId,
    });
    if (intent === "callback") {
      const ack = await visitor<{ message: string }>(token, "/callbacks", {
        conversation_id: conversationId,
        reason: reason ?? "Asked to talk to a person",
        queue: queue ?? "admission",
        department: queue === "faculty" ? (department ?? null) : null,
        language: lang,
        preferred_time: form.preferred_time || null,
        preferred_channel: preferredChannel,
        transcript: transcript.slice(-20),
      });
      setDoneMessage(ack.message);
    } else {
      setDoneMessage(t.enquiryDone(tenant.response_time_hours));
    }
    setStep("done");
  }

  const submitAdult = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.phone && !form.email) return setError(t.needContact);
    if (form.channel === "whatsapp" && !form.phone) return setError(t.whatsappNeedsPhone);
    void run(async () => {
      if (form.channel === "whatsapp") {
        await visitor(token, "/consents", { purpose: "whatsapp", granted: true, text_version: tv });
      }
      await finish(false, form.channel);
    });
  };

  const startGuardian = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await visitor<{ verification_id: string; message: string; dev_code: string | null }>(
        token,
        "/consents/guardian/start",
        { channel: form.guardianChannel, destination: form.guardianDest, language: lang },
      );
      setVerification({ id: r.verification_id, devCode: r.dev_code, message: r.message });
      setStep("code");
    });
  };

  const verifyCode = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await visitor(token, "/consents/guardian/verify", {
        verification_id: verification?.id,
        code: form.code.trim(),
        purposes: ["contact"],
        text_version: tv,
      });
      setStep("minor");
    });
  };

  const submitMinor = (e: React.FormEvent) => {
    e.preventDefault();
    void run(() => finish(true, form.guardianChannel));
  };

  const title = step === "done" ? t.allSet : intent === "callback" ? t.talkTitle : t.enquireTitle;

  return (
    <section aria-labelledby="contact-title" className="flex h-full flex-col overflow-y-auto p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="contact-title" className="text-lg font-bold text-accent">
          {title}
        </h2>
        {step !== "done" && (
          <button className="btn-ghost px-2 py-1" onClick={() => onClose()} aria-label={t.backToChat}>
            {t.backToChat}
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      )}

      {step === "consent" && (
        <div className="space-y-4 text-sm">
          <p>{t.consentText(tenant.name)}</p>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]"
              checked={contactOk}
              onChange={(e) => setContactOk(e.target.checked)}
            />
            <span>{t.consentContact}</span>
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]"
              checked={marketing}
              onChange={(e) => setMarketing(e.target.checked)}
            />
            <span>{t.consentMarketing}</span>
          </label>
          {tenant.privacy_notice_url && (
            <a
              className="text-brand2 underline"
              href={tenant.privacy_notice_url}
              target="_blank"
              rel="noreferrer"
            >
              {t.privacy}
            </a>
          )}
          <div className="flex gap-2">
            <button className="btn-primary" disabled={!contactOk || busy} onClick={agree}>
              {t.agree}
            </button>
            <button className="btn-ghost" onClick={() => onClose()}>
              {t.notNow}
            </button>
          </div>
        </div>
      )}

      {step === "age" && (
        <fieldset className="space-y-3 text-sm">
          <legend className="mb-2">{t.ageQ}</legend>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={() => setStep("adult")}>
              {t.adult}
            </button>
            <button className="btn-secondary" onClick={() => setStep("guardian")}>
              {t.minor}
            </button>
          </div>
        </fieldset>
      )}

      {step === "adult" && (
        <form className="space-y-3" onSubmit={submitAdult} noValidate>
          <Field label={t.name} id="name">
            <input id="name" className="field" autoComplete="name" value={form.name} onChange={set("name")} />
          </Field>
          <Field label={t.phone} id="phone" hint={t.phoneHint}>
            <input
              id="phone"
              className="field"
              type="tel"
              autoComplete="tel"
              inputMode="tel"
              value={form.phone}
              onChange={set("phone")}
            />
          </Field>
          <Field label={t.email} id="email">
            <input
              id="email"
              className="field"
              type="email"
              autoComplete="email"
              value={form.email}
              onChange={set("email")}
            />
          </Field>
          <ProgramField tenant={tenant} t={t} value={form.program} onChange={set("program")} />
          <fieldset>
            <legend className="label">{t.reachBy}</legend>
            <div className="flex flex-wrap gap-3 text-sm">
              {(["phone", "email", "whatsapp"] as Channel[]).map((c) => (
                <label key={c} className="flex items-center gap-1">
                  <input
                    type="radio"
                    name="channel"
                    value={c}
                    checked={form.channel === c}
                    onChange={set("channel")}
                    className="accent-[rgb(var(--brand))]"
                  />
                  {c === "phone" ? t.byPhone : c === "email" ? t.byEmail : t.byWhatsapp}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label={t.bestTime} id="ptime">
            <input
              id="ptime"
              className="field"
              placeholder={t.bestTimeEg}
              value={form.preferred_time}
              onChange={set("preferred_time")}
            />
          </Field>
          <button className="btn-primary w-full" disabled={busy}>
            {intent === "callback" ? t.requestCallback : t.sendEnquiry}
          </button>
        </form>
      )}

      {step === "guardian" && (
        <form className="space-y-3 text-sm" onSubmit={startGuardian}>
          <p>{t.guardianIntro}</p>
          <fieldset>
            <legend className="label">{t.sendCodeBy}</legend>
            <div className="flex gap-3">
              {(["email", "whatsapp"] as const).map((c) => (
                <label key={c} className="flex items-center gap-1">
                  <input
                    type="radio"
                    name="gch"
                    value={c}
                    checked={form.guardianChannel === c}
                    onChange={set("guardianChannel")}
                    className="accent-[rgb(var(--brand))]"
                  />
                  {c === "email" ? t.byEmail : t.byWhatsapp}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label={form.guardianChannel === "email" ? t.guardianEmail : t.guardianWhatsapp} id="gdest">
            <input
              id="gdest"
              className="field"
              type={form.guardianChannel === "email" ? "email" : "tel"}
              required
              value={form.guardianDest}
              onChange={set("guardianDest")}
            />
          </Field>
          <button className="btn-primary w-full" disabled={busy || !form.guardianDest}>
            {t.sendCode}
          </button>
        </form>
      )}

      {step === "code" && verification && (
        <form className="space-y-3 text-sm" onSubmit={verifyCode}>
          <p>
            {lang === "en"
              ? `${verification.message} `
              : form.guardianChannel === "whatsapp"
                ? `${t.waGuardianHint} `
                : ""}
            {t.askCode}
          </p>
          {verification.devCode && (
            <p className="rounded-lg bg-yellow-50 px-3 py-2 text-yellow-900">
              {t.devCode} <strong>{verification.devCode}</strong>
            </p>
          )}
          <Field label={t.code} id="code">
            <input
              id="code"
              className="field tracking-widest"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              value={form.code}
              onChange={set("code")}
            />
          </Field>
          <button className="btn-primary w-full" disabled={busy || form.code.length < 4}>
            Confirm
          </button>
        </form>
      )}

      {step === "minor" && (
        <form className="space-y-3" onSubmit={submitMinor}>
          <p className="text-sm text-green-800">{t.guardianOk}</p>
          <Field label={t.firstName} id="mname">
            <input id="mname" className="field" value={form.name} onChange={set("name")} />
          </Field>
          <ProgramField tenant={tenant} t={t} value={form.program} onChange={set("program")} />
          <Field label={t.parentTime} id="mtime">
            <input
              id="mtime"
              className="field"
              value={form.preferred_time}
              onChange={set("preferred_time")}
            />
          </Field>
          <button className="btn-primary w-full" disabled={busy}>
            {intent === "callback" ? t.requestCallback : t.sendEnquiry}
          </button>
        </form>
      )}

      {step === "done" && (
        <div className="space-y-4 text-sm" role="status">
          <p className="rounded-lg bg-green-50 px-3 py-3 text-green-900">{doneMessage}</p>
          <button className="btn-primary" onClick={() => onClose(doneMessage)}>
            {t.backToChat.replace("← ", "")}
          </button>
        </div>
      )}
    </section>
  );
}

function Field({
  label,
  id,
  hint,
  children,
}: {
  label: string;
  id: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

function ProgramField({
  tenant,
  t,
  value,
  onChange,
}: {
  tenant: TenantInfo;
  t: Strings;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void;
}) {
  const options = tenant.topics.filter((x) => /programme|entry/i.test(x));
  return (
    <div>
      <label htmlFor="program" className="label">
        {t.interested}
      </label>
      <select id="program" className="field" value={value} onChange={onChange}>
        <option value="">{t.notSure}</option>
        {(options.length ? options : ["Undergraduate", "Postgraduate"]).map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}
