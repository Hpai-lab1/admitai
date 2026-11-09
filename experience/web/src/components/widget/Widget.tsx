"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { STRINGS, detectLang, isRephrase, type Lang } from "@/lib/i18n";
import { themeStyle } from "@/lib/theme";
import {
  ask,
  isLong,
  logEvent,
  quickReplies,
  recoverAnswer,
  StreamCut,
  type HandoffRequest,
  type LeadPrompt,
  startSession,
  visitor,
  type Source,
  type TenantInfo,
  type WidgetSession,
} from "@/lib/widget-api";
import { staffAccessToken } from "@/lib/api";
import { VoiceError, play, record, voiceTurn } from "@/lib/voice";
import { ContactFlow } from "./ContactFlow";

type Msg = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  sources?: Source[];
  messageId?: string;
  traceId?: string;
  offerHandoff?: boolean;
  handoff?: HandoffRequest;
  leadPrompt?: LeadPrompt;
  feedback?: "down" | "other" | "sent";
  streaming?: boolean;
  expanded?: boolean;
};

const REASONS = [
  "wrong",
  "outdated",
  "incomplete",
  "not_what_i_asked",
  "hard_to_understand",
  "other",
] as const;
type Reason = (typeof REASONS)[number];
const SIZES = [0.9, 1, 1.15];

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());

export function Widget({
  publicKey,
  themeOverride,
  fullPage = false,
}: {
  publicKey: string;
  themeOverride?: Partial<TenantInfo>;
  fullPage?: boolean;
}) {
  const [session, setSession] = useState<WidgetSession | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [confirmErase, setConfirmErase] = useState(false);
  const [busy, setBusy] = useState(false);
  const [contact, setContact] = useState<null | {
    intent: "callback" | "enquiry";
    reason?: string;
    handoff?: HandoffRequest;
    programs?: string[];
    score?: number;
  }>(null);
  // Module B said the visitor is under 18 (PRD F5.3): the contact flow goes straight to a guardian.
  const [guardianRequired, setGuardianRequired] = useState(false);
  const [size, setSize] = useState(1);
  const [lang, setLang] = useState<Lang>("en");
  // Voice orb states (PRD 13.4): idle, listening, thinking, speaking, error.
  const [voice, setVoice] = useState<"idle" | "listening" | "thinking" | "speaking" | "error">("idle");
  const listening = voice === "listening";
  const [caption, setCaption] = useState("");
  const [rating, setRating] = useState<"closed" | "open" | "done">("closed");
  const conversationId = useRef(uid());
  const listRef = useRef<HTMLOListElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stopRec = useRef<null | (() => Promise<Blob>)>(null);
  const stopAudio = useRef<null | (() => void)>(null);
  const t = STRINGS[lang];

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const utm: Record<string, string> = {};
    params.forEach((v, k) => {
      if (k.startsWith("utm_")) utm[k] = v;
    });
    startSession(
      publicKey,
      utm,
      params.get("ref") || document.referrer || null,
      // Console preview: a college that is not live yet opens only for its own staff (F10.4).
      params.get("preview") === "1" ? staffAccessToken() : null,
    )
      .then((s) => {
        setSession(s);
        setMessages([{ id: uid(), role: "assistant", text: s.tenant.greeting }]);
      })
      .catch((e: Error) => setLoadError(e.message));
  }, [publicKey]);

  useEffect(() => {
    document.documentElement.style.setProperty("--text-scale", String(SIZES[size]));
  }, [size]);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    // Esc inside the iframe closes the widget too (keyboard users)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !fullPage) window.parent?.postMessage({ type: "admitai:close" }, "*");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullPage]);

  const tenant: TenantInfo | null = session ? { ...session.tenant, ...(themeOverride ?? {}) } : null;
  const token = session?.session_token ?? "";
  const event = useCallback(
    (kind: string, messageId?: string) => token && logEvent(token, conversationId.current, kind, messageId),
    [token],
  );

  const send = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || !session || busy) return;
      // auto-detect Hindi typed in Devanagari (manual switch stays available)
      const typed = detectLang(q);
      const answerLang: Lang = typed === "hi" ? "hi" : lang;
      if (typed === "hi" && lang !== "hi") setLang("hi");
      const previous = [...messages].reverse().find((m) => m.role === "user")?.text;
      event("user_message");
      if (previous && isRephrase(previous, q)) event("rephrase");
      setInput("");
      setBusy(true);
      const answerId = uid();
      setMessages((m) => [
        ...m,
        { id: uid(), role: "user", text: q },
        { id: answerId, role: "assistant", text: "", streaming: true },
      ]);
      const patch = (fn: (m: Msg) => Msg) =>
        setMessages((all) => all.map((m) => (m.id === answerId ? fn(m) : m)));
      try {
        const done = await ask(
          conversationId.current,
          q,
          session.session_token,
          (tok) => patch((m) => ({ ...m, text: m.text + tok })),
          answerLang,
        );
        event("answer", done.message_id);
        patch((m) => ({
          ...m,
          text: m.text.trim(),
          streaming: false,
          sources: done.sources,
          messageId: done.message_id,
          traceId: done.trace_id,
          offerHandoff: done.offer_handoff,
          handoff: done.handoff,
          leadPrompt: done.lead_prompt,
        }));
        if (done.guardian_required) setGuardianRequired(true);
      } catch (e) {
        if (!(e instanceof StreamCut)) {
          patch((m) => ({ ...m, streaming: false, offerHandoff: true, text: (e as Error).message }));
          return;
        }
        // The connection dropped: Module B still finishes the answer, so read it back.
        patch((m) => ({ ...m, text: t.reconnecting }));
        const got = await recoverAnswer(conversationId.current, q, session.session_token);
        patch((m) =>
          got
            ? {
                ...m,
                text: got.text,
                streaming: false,
                sources: got.sources,
                messageId: got.message_id,
                traceId: got.trace_id,
                offerHandoff: got.offer_handoff,
              }
            : { ...m, streaming: false, offerHandoff: true, text: t.lostAnswer },
        );
        if (got) event("answer", got.message_id);
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [session, busy, lang, messages, event, t],
  );

  function openContact(
    intent: "callback" | "enquiry",
    reason?: string,
    extra: { handoff?: HandoffRequest; programs?: string[]; score?: number } = {},
  ) {
    if (intent === "callback") event("talk_to_human");
    setContact({ intent, reason, ...extra });
  }

  async function rate(m: Msg, value: 1 | -1, reason?: Reason, comment?: string) {
    if (!session || !m.messageId) return;
    const next: Msg["feedback"] =
      value === 1 || (reason && reason !== "other") || comment !== undefined
        ? "sent"
        : reason === "other"
          ? "other"
          : "down";
    setMessages((all) => all.map((x) => (x.id === m.id ? { ...x, feedback: next } : x)));
    if (next !== "sent") return; // wait for the reason (and the text for "other")
    await visitor(session.session_token, "/feedback", {
      conversation_id: conversationId.current,
      message_id: m.messageId,
      kind: "thumb",
      value,
      reason: reason ?? null,
      comment: comment || null,
      trace_id: m.traceId ?? null,
    }).catch(() => {});
  }

  async function rateChat(stars: number) {
    setRating("done");
    await visitor(token, "/feedback", {
      conversation_id: conversationId.current,
      message_id: "conversation",
      kind: "rating",
      value: stars,
    }).catch(() => {});
  }

  async function withdraw() {
    if (!session) return;
    const r = await visitor(session.session_token, "/consents/withdraw", {}).catch(() => null);
    setMessages((m) => [...m, { id: uid(), role: "system", text: r ? t.withdrawn : t.failed }]);
  }

  // Erasure on request (PRD 11.4): two taps, so a stray click cannot wipe the conversation.
  async function erase() {
    if (!session) return;
    if (!confirmErase) {
      setConfirmErase(true);
      return;
    }
    setConfirmErase(false);
    const r = await visitor(session.session_token, "/privacy/erase", {}).catch(() => null);
    setMessages(
      r
        ? [{ id: uid(), role: "system", text: t.erased }]
        : (m) => [...m, { id: uid(), role: "system", text: t.failed }],
    );
  }

  async function toggleVoice() {
    if (!session) return;
    if (voice === "speaking") {
      stopAudio.current?.(); // barge-in: stop the spoken answer and listen again
    }
    if (voice === "listening" && stopRec.current) {
      const blob = await stopRec.current();
      stopRec.current = null;
      setVoice("thinking");
      setCaption(t.thinking);
      try {
        const r = await voiceTurn(session.session_token, conversationId.current, blob, lang);
        event("user_message");
        setMessages((m) => [
          ...m,
          { id: uid(), role: "user", text: r.transcript || "…" },
          {
            id: uid(),
            role: "assistant",
            text: r.answer,
            sources: r.sources,
            messageId: r.message_id,
            traceId: r.trace_id,
            offerHandoff: r.offer_handoff,
            handoff: r.events?.handoff_requested,
          },
        ]);
        if (r.audio_wav_base64) {
          setVoice("speaking");
          setCaption(t.speaking);
          stopAudio.current = play(r.audio_wav_base64, () => {
            stopAudio.current = null;
            setVoice((v) => (v === "speaking" ? "idle" : v));
            setCaption("");
          });
        } else {
          setVoice("idle"); // PRD F2: if speech fails, the text answer is still shown
          setCaption("");
        }
      } catch (e) {
        setVoice("error");
        setCaption(e instanceof VoiceError && e.kind === "busy" ? e.message : t.failed);
      }
      return;
    }
    try {
      stopRec.current = await record();
      setVoice("listening");
      setCaption(t.listening);
    } catch (e) {
      setVoice("error");
      setCaption(e instanceof VoiceError && e.kind === "denied" ? t.micDenied : t.noVoice);
    }
  }

  const close = () => window.parent?.postMessage({ type: "admitai:close" }, "*");

  if (loadError) {
    return (
      <div role="alert" className="p-6 text-sm text-red-800">
        The assistant could not start: {loadError}
      </div>
    );
  }
  if (!tenant || !session) {
    return (
      <div className="space-y-3 p-6" role="status">
        <span className="sr-only">{t.loading}</span>
        <div className="skeleton h-12" />
        <div className="skeleton h-16 w-3/4" />
        <div className="skeleton h-10 w-1/2" />
      </div>
    );
  }

  const transcript = messages
    .filter((m) => m.role !== "system" && m.text)
    .map((m) => ({ role: m.role as "user" | "assistant", text: m.text }));
  const answered = messages.some((m) => m.role === "assistant" && m.messageId);
  const lastMsg = messages[messages.length - 1];
  const quick =
    !busy && lastMsg?.role === "assistant" && lastMsg.messageId && messages.length > 1
      ? quickReplies(
          tenant.starter_questions.length ? tenant.starter_questions : tenant.topics,
          messages.filter((m) => m.role === "user").map((m) => m.text),
        )
      : [];

  return (
    <div
      style={themeStyle(tenant)}
      className={`flex flex-col bg-paper font-brand text-ink ${fullPage ? "mx-auto min-h-dvh max-w-3xl" : "h-dvh"}`}
    >
      {/* header: like the college site's top bar */}
      <header className="brand-gradient flex items-center gap-2 px-3 py-2">
        {tenant.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={tenant.logo_url} alt="" className="h-8 w-8 rounded bg-white object-contain p-0.5" />
        ) : (
          <span
            aria-hidden
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/20 text-lg font-bold"
          >
            {tenant.name.charAt(0)}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-bold leading-tight">{tenant.assistant_name}</h1>
          <p className="truncate text-xs opacity-90">{tenant.name}</p>
        </div>
        <div
          role="group"
          aria-label={t.language}
          className="flex overflow-hidden rounded-md border border-white/40 text-xs"
        >
          {(
            [
              ["en", "EN", "English"],
              ["hi", "हिं", "हिन्दी"],
            ] as const
          ).map(([code, label, name]) => (
            <button
              key={code}
              lang={code}
              onClick={() => setLang(code)}
              aria-pressed={lang === code}
              aria-label={name}
              className={`min-h-11 min-w-11 px-2 ${lang === code ? "bg-white text-[rgb(var(--accent))]" : "hover:bg-white/15"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div
          role="group"
          aria-label={t.textSize}
          className="hidden overflow-hidden rounded-md border border-white/40 text-xs sm:flex"
        >
          {["A-", "A", "A+"].map((label, i) => (
            <button
              key={label}
              onClick={() => setSize(i)}
              aria-pressed={size === i}
              aria-label={[t.smaller, t.normal, t.larger][i]}
              className={`min-h-11 min-w-9 px-1.5 ${size === i ? "bg-white text-[rgb(var(--accent))]" : "hover:bg-white/15"}`}
            >
              {label}
            </button>
          ))}
        </div>
        {!fullPage && (
          <button
            onClick={close}
            aria-label={t.close}
            className="grid h-11 w-11 place-items-center rounded-md text-xl leading-none hover:bg-white/15"
          >
            ×
          </button>
        )}
      </header>

      {tenant.announcement && (
        <p className="bg-accent px-4 py-1.5 text-center text-xs font-medium text-white">
          {tenant.announcement}
        </p>
      )}

      {contact ? (
        <div className="min-h-0 flex-1 bg-surface">
          <ContactFlow
            intent={contact.intent}
            reason={contact.reason}
            queue={contact.handoff?.queue}
            department={contact.handoff?.department ?? undefined}
            programs={contact.programs}
            score={contact.score}
            knownMinor={guardianRequired}
            token={session.session_token}
            tenant={tenant}
            lang={lang}
            conversationId={conversationId.current}
            transcript={transcript}
            onClose={(msg) => {
              setContact(null);
              if (msg) setMessages((m) => [...m, { id: uid(), role: "system", text: msg }]);
            }}
          />
        </div>
      ) : (
        <>
          <ol
            ref={listRef}
            aria-live="polite"
            aria-relevant="additions text"
            aria-label="Conversation"
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4"
          >
            {messages.map((m, i) => (
              <li key={m.id} className={m.role === "user" ? "flex justify-end" : "flex"}>
                {m.role === "system" ? (
                  <p className="mx-auto max-w-[90%] rounded-lg bg-green-50 px-3 py-2 text-center text-sm text-green-900">
                    {m.text}
                  </p>
                ) : (
                  <div
                    lang={detectLang(m.text) ?? lang}
                    className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm shadow-sm ${
                      m.role === "user" ? "rounded-br-sm bg-brand2 text-white" : "rounded-bl-sm bg-surface"
                    }`}
                  >
                    <span className="sr-only">{m.role === "user" ? t.you : t.assistant}</span>
                    <p
                      id={`text-${m.id}`}
                      className={`whitespace-pre-wrap ${!m.streaming && !m.expanded && isLong(m.text) ? "line-clamp-[12]" : ""}`}
                    >
                      <AnswerText text={m.text || (m.streaming ? "…" : "")} sources={m.sources} />
                    </p>
                    {!m.streaming && isLong(m.text) && (
                      <button
                        className="mt-1 min-h-11 text-xs font-semibold text-brand hover:underline"
                        aria-expanded={!!m.expanded}
                        aria-controls={`text-${m.id}`}
                        onClick={() =>
                          setMessages((all) =>
                            all.map((x) => (x.id === m.id ? { ...x, expanded: !x.expanded } : x)),
                          )
                        }
                      >
                        {m.expanded ? t.showLess : t.showMore}
                      </button>
                    )}
                    {m.offerHandoff && (
                      <button
                        className="btn-primary mt-2 px-3 text-xs"
                        onClick={() =>
                          openContact(
                            "callback",
                            `${m.handoff?.reason ?? "Couldn't answer"}: ${messages[i - 1]?.text ?? ""}`.slice(
                              0,
                              300,
                            ),
                            { handoff: m.handoff },
                          )
                        }
                      >
                        {t.askSomeone}
                      </button>
                    )}
                    {m.role === "assistant" && m.messageId && (
                      <Feedback msg={m} t={t} onRate={(v, r, c) => void rate(m, v, r, c)} />
                    )}
                    {m.leadPrompt && (
                      // Inline lead card, never a blocking modal (PRD 13.4); asked once (Module B's rule).
                      <div className="mt-3 rounded-xl border border-brand2/30 bg-brand2/5 p-3">
                        <p className="text-sm">{t.leadAsk}</p>
                        <button
                          className="btn-primary mt-2 px-3 text-xs"
                          onClick={() =>
                            openContact("enquiry", undefined, {
                              programs: m.leadPrompt?.program_interest,
                              score: m.leadPrompt?.score,
                            })
                          }
                        >
                          {t.leadYes}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </li>
            ))}
            {rating === "open" && (
              <li className="flex">
                <fieldset className="rounded-2xl bg-surface px-3.5 py-2.5 text-sm shadow-sm">
                  <legend className="sr-only">{t.rateQ}</legend>
                  <p aria-hidden className="mb-1">
                    {t.rateQ}
                  </p>
                  <div className="flex">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        aria-label={t.stars(n)}
                        onClick={() => void rateChat(n)}
                        className="grid h-11 w-11 place-items-center rounded text-xl text-amber-500 hover:bg-subtle"
                      >
                        ★
                      </button>
                    ))}
                  </div>
                </fieldset>
              </li>
            )}
            {rating === "done" && (
              <li className="text-center text-xs text-muted" role="status">
                {t.rateThanks}
              </li>
            )}
          </ol>

          {messages.length <= 1 && (
            <div className="px-4 pb-2">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-accent">{t.admissions}</p>
              <div className="flex flex-wrap gap-2">
                {(tenant.topics.length ? tenant.topics : tenant.starter_questions).map((topic) => (
                  <button key={topic} className="chip" onClick={() => void send(topic)}>
                    {topic}
                  </button>
                ))}
              </div>
            </div>
          )}

          {quick.length > 0 && !contact && (
            <div className="px-4 pb-2">
              <p className="mb-1 text-xs text-muted">{t.moreQuestions}</p>
              <div className="flex flex-wrap gap-2">
                {quick.map((q) => (
                  <button key={q} className="chip" onClick={() => void send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {listening && (
            <p
              className="mx-4 mb-1 rounded-lg bg-black/80 px-3 py-1.5 text-sm text-white"
              aria-live="assertive"
            >
              🎙 {caption}
            </p>
          )}
          {!listening && caption && !input && (
            <p className="mx-4 mb-1 text-xs text-muted" aria-live="polite" data-voice={voice}>
              {caption}
            </p>
          )}

          <form
            className="flex items-center gap-2 border-t border-line bg-surface px-3 py-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <button
              type="button"
              onClick={() => void toggleVoice()}
              disabled={voice === "thinking"}
              aria-pressed={listening}
              aria-label={listening ? t.stopSpeak : t.speak}
              className={`grid h-11 w-11 shrink-0 place-items-center rounded-full ${
                listening ? "animate-pulse bg-brand text-white" : "bg-paper text-accent hover:bg-subtle"
              }`}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden fill="currentColor">
                <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
              </svg>
            </button>
            <label htmlFor="q" className="sr-only">
              {t.placeholder}
            </label>
            <input
              id="q"
              ref={inputRef}
              className="field flex-1"
              placeholder={t.placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              maxLength={500}
              autoComplete="off"
            />
            <button className="btn-primary" disabled={busy || !input.trim()}>
              {t.ask}
            </button>
          </form>

          <nav
            aria-label="More options"
            className="flex flex-wrap items-center gap-x-1 bg-surface px-2 pb-2 text-xs"
          >
            <LinkButton onClick={() => openContact("enquiry")}>{t.enquire}</LinkButton>
            <LinkButton onClick={() => openContact("callback")}>{t.talk}</LinkButton>
            {tenant.apply_url && (
              <a
                className="inline-flex min-h-11 items-center px-2 font-semibold text-brand underline-offset-2 hover:underline"
                href={tenant.apply_url}
                target="_blank"
                rel="noreferrer"
              >
                {t.apply} ↗
              </a>
            )}
            {answered && rating === "closed" && (
              <LinkButton onClick={() => setRating("open")}>{t.rate}</LinkButton>
            )}
            <button
              className="ml-auto min-h-11 px-2 text-muted hover:underline"
              onClick={() => void withdraw()}
            >
              {t.withdraw}
            </button>
            <button
              className={`min-h-11 px-2 hover:underline ${confirmErase ? "font-semibold text-red-700" : "text-muted"}`}
              onClick={() => void erase()}
              onBlur={() => setConfirmErase(false)}
            >
              {confirmErase ? t.eraseConfirm : t.erase}
            </button>
          </nav>
        </>
      )}
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      className="min-h-11 px-2 font-semibold text-brand underline-offset-2 hover:underline"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

/** Numbered citation chip; tap or hover shows the document title and year (PRD 13.4). */
/** Answer text with Module B's inline [n] markers shown as citation chips (PRD F1.2, 13.4).
 *  Answers without markers (older services, mocks) get their chips at the end. */
/** Light Markdown for answers (PRD F1.2): **bold**, *italic* or _italic_, `code`, "# " headings
 *  as bold lines and "- " / "* " list markers as bullets. Line breaks are kept by the bubble's
 *  pre-wrap, so long answers still collapse after 12 lines. Builds React elements only, never
 *  HTML, so model output cannot inject markup. */
export function markdownInline(text: string): React.ReactNode[] {
  const lines = text
    .split("\n")
    .map((l) => l.replace(/^(\s*)[-*]\s+/, "$1• ").replace(/^#{1,6}\s+(.*)$/, "**$1**"));
  const out: React.ReactNode[] = [];
  const re = /\*\*([^*\n]+)\*\*|`([^`\n]+)`|(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])/g;
  lines.forEach((line, li) => {
    if (li) out.push("\n");
    let last = 0;
    for (const m of line.matchAll(re)) {
      if (m.index > last) out.push(line.slice(last, m.index));
      const k = `${li}-${m.index}`;
      if (m[1] !== undefined) out.push(<strong key={k}>{m[1]}</strong>);
      else if (m[2] !== undefined)
        out.push(
          <code key={k} className="rounded bg-subtle px-1 text-[0.9em]">
            {m[2]}
          </code>,
        );
      else out.push(<em key={k}>{m[3]}</em>);
      last = m.index + m[0].length;
    }
    if (last < line.length) out.push(line.slice(last));
  });
  return out;
}

export function AnswerText({ text, sources }: { text: string; sources?: Source[] }) {
  const parts = text.split(/\s*\[(\d{1,2})\]/);
  if (parts.length === 1 || !sources?.length) {
    return (
      <>
        {markdownInline(text)}
        {sources?.map((s, n) => <Citation key={n} n={n + 1} source={s} />)}
      </>
    );
  }
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) return <span key={i}>{markdownInline(part)}</span>;
        const n = Number(part);
        const source = sources[n - 1];
        return source ? <Citation key={i} n={n} source={source} /> : null;
      })}
    </>
  );
}

function Citation({ n, source }: { n: number; source: Source }) {
  const [open, setOpen] = useState(false);
  const section = source.heading_path ? ` > ${source.heading_path.split(" > ").pop()}` : "";
  const label = `${source.title}${section}${source.academic_year ? ` (${source.academic_year})` : ""}`;
  return (
    <span className="relative">
      <sup>
        <button
          className="ml-0.5 inline-grid h-6 min-w-6 place-items-center rounded-full bg-brand2/15 px-1 text-[10px] font-bold text-brand2 hover:bg-brand2 hover:text-white"
          aria-label={`Source ${n}: ${label}`}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onBlur={() => setOpen(false)}
        >
          {n}
        </button>
      </sup>
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-6 left-0 z-10 w-56 rounded-lg bg-ink px-3 py-2 text-xs text-surface shadow-lg"
        >
          {label}
        </span>
      )}
    </span>
  );
}

function Feedback({
  msg,
  t,
  onRate,
}: {
  msg: Msg;
  t: (typeof STRINGS)["en"];
  onRate: (v: 1 | -1, reason?: Reason, comment?: string) => void;
}) {
  const [text, setText] = useState("");
  if (msg.feedback === "sent") return <p className="mt-1.5 text-xs text-muted">{t.thanks}</p>;
  if (msg.feedback === "other") {
    return (
      <form
        className="mt-2 flex gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          onRate(-1, "other", text.trim());
        }}
      >
        <label htmlFor={`other-${msg.id}`} className="sr-only">
          {t.tellUs}
        </label>
        <input
          id={`other-${msg.id}`}
          className="field text-xs"
          placeholder={t.tellUs}
          maxLength={1000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
        />
        <button className="btn-secondary text-xs">{t.send}</button>
      </form>
    );
  }
  if (msg.feedback === "down") {
    return (
      <fieldset className="mt-2">
        <legend className="mb-1 text-xs text-muted">{t.whatWrong}</legend>
        <div className="flex flex-wrap gap-1">
          {REASONS.map((value) => (
            <button key={value} className="chip px-3 text-xs" onClick={() => onRate(-1, value)}>
              {t.reasons[value]}
            </button>
          ))}
        </div>
      </fieldset>
    );
  }
  return (
    <div className="mt-1 flex" role="group" aria-label={t.wasHelpful}>
      <button
        className="grid h-11 w-11 place-items-center rounded text-base hover:bg-subtle"
        aria-label={t.helpful}
        onClick={() => onRate(1)}
      >
        👍
      </button>
      <button
        className="grid h-11 w-11 place-items-center rounded text-base hover:bg-subtle"
        aria-label={t.notHelpful}
        onClick={() => onRate(-1)}
      >
        👎
      </button>
    </div>
  );
}
