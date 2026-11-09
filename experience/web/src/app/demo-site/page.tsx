"use client";

import Script from "next/script";
import { useEffect, useState } from "react";
import { themeStyle } from "@/lib/theme";

// A made-up college homepage laid out like a typical Indian university admissions site, used to show
// the widget embedded with one <script> tag. Fake names and contact details only.
const THEME = {
  primary_color: "#E34A21",
  secondary_color: "#992361",
  accent_color: "#361C6A",
  font: "montserrat" as const,
};
const ADMISSIONS = [
  "UG Programmes",
  "PG Programmes",
  "Doctoral Programmes",
  "Lateral Entry",
  "Admission Process",
  "Scholarships",
  "Loan Assistance",
  "Admission FAQs",
];
const NOTICES = [
  ["24 Oct 2026", "Counselling schedule for B.Tech lateral entry"],
  ["20 Oct 2026", "Fee payment window for the odd semester extended"],
  ["15 Oct 2026", "Hostel allotment list for first-year students"],
];
const PROGRAMMES = [
  ["B.Tech Computer Science", "4 years · Undergraduate", "Engineering"],
  ["B.Tech Mechanical", "4 years · Undergraduate", "Engineering"],
  ["M.Tech Data Science", "2 years · Postgraduate", "Engineering"],
  ["MBA", "2 years · Postgraduate", "Management"],
];

const openChat = () => (window as unknown as { AdmitAI?: { open: () => void } }).AdmitAI?.open();

export default function DemoSite() {
  const [size, setSize] = useState(1);
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    document.documentElement.style.setProperty("--text-scale", String([0.9, 1, 1.15][size]));
  }, [size]);

  return (
    // `light`: this stands in for the college's own page, so it keeps its light colours even when
    // the visitor's system is in dark mode (globals.css); otherwise the menu text turns light on
    // the white header and becomes unreadable.
    <div style={themeStyle(THEME)} className="light min-h-screen bg-surface font-brand text-ink">
      <a href="#main" className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-surface px-3 py-2">
        Skip to content
      </a>
      {/* top bar */}
      <div className="bg-accent text-xs text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2">
          <a href="#" className="hover:underline">
            Virtual Tour
          </a>
          <a href="#" className="hover:underline">
            Student Portal
          </a>
          <div
            role="group"
            aria-label="Text size"
            className="flex overflow-hidden rounded border border-white/40"
          >
            {["A-", "A", "A+"].map((l, i) => (
              <button
                key={l}
                aria-pressed={size === i}
                onClick={() => setSize(i)}
                className={`px-2 py-0.5 ${size === i ? "bg-white text-[rgb(var(--accent))]" : ""}`}
              >
                {l}
              </button>
            ))}
          </div>
          <span className="ml-auto">Admissions helpline: +1 555 010 0000</span>
          <a href="#" className="btn-primary px-3 py-1 text-xs">
            Apply Now
          </a>
        </div>
      </div>

      {/* header + navigation */}
      <header className="sticky top-0 z-30 border-b border-line bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="brand-gradient grid h-10 w-10 place-items-center rounded-full text-lg font-extrabold">
              D
            </span>
            <div className="leading-tight">
              <p className="font-extrabold text-accent">Demo College</p>
              <p className="text-[11px] uppercase tracking-wider text-muted">of Engineering</p>
            </div>
          </div>
          <nav
            aria-label="Main"
            className="ml-auto hidden items-center gap-5 text-sm font-semibold text-ink md:flex"
          >
            <a href="#">About Us</a>
            <div className="relative">
              <button
                aria-expanded={menu}
                aria-controls="admissions-menu"
                onClick={() => setMenu((m) => !m)}
                className="font-semibold uppercase text-brand"
              >
                Admissions ▾
              </button>
              {menu && (
                <ul
                  id="admissions-menu"
                  className="absolute right-0 top-8 w-56 rounded-lg border border-line bg-surface py-2 shadow-lg"
                >
                  {ADMISSIONS.map((a) => (
                    <li key={a}>
                      <a
                        href="#"
                        onClick={(e) => {
                          if (a === "Admission FAQs") {
                            e.preventDefault();
                            openChat();
                          }
                        }}
                        className="block px-4 py-1.5 text-sm font-medium hover:bg-paper hover:text-brand"
                      >
                        {a}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <a href="#">Academics</a>
            <a href="#programmes">Programmes</a>
            <a href="#">Research</a>
            <a href="#">Student Life</a>
            <a href="#">Placements</a>
          </nav>
        </div>
      </header>

      <main id="main">
        {/* hero */}
        <section className="brand-gradient">
          <div className="mx-auto grid max-w-6xl gap-8 px-4 py-16 md:grid-cols-2 md:py-24">
            <div>
              <p className="mb-3 inline-block rounded-full bg-white/20 px-3 py-1 text-xs font-semibold uppercase tracking-wider">
                Admissions open · 2026
              </p>
              <h1 className="text-4xl font-extrabold leading-tight md:text-5xl">
                Admissions Open for 2026 – UG and PG Programmes
              </h1>
              <p className="mt-4 max-w-lg text-lg opacity-95">
                Learn, build and lead with industry-ready programmes on a modern campus.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a href="#" className="btn bg-white px-6 py-3 text-base text-brand hover:bg-white/90">
                  Apply Now
                </a>
                <button
                  onClick={openChat}
                  className="btn border-2 border-white px-6 py-3 text-base hover:bg-white/10"
                >
                  Ask our admissions assistant
                </button>
              </div>
            </div>
            <div className="hidden rounded-2xl bg-white/10 p-6 md:block">
              <p className="text-sm font-semibold uppercase tracking-wider opacity-90">
                Questions students ask
              </p>
              <ul className="mt-4 space-y-3">
                {[
                  "What is the fee for B.Tech?",
                  "Is there a hostel for first years?",
                  "Can diploma holders join?",
                ].map((q) => (
                  <li key={q} className="rounded-lg bg-white/15 px-4 py-3 text-sm">
                    “{q}”
                  </li>
                ))}
              </ul>
              <p className="mt-4 text-sm opacity-90">
                Answered 24×7, in seconds, from official college documents.
              </p>
            </div>
          </div>
        </section>

        {/* notices strip */}
        <section aria-labelledby="notices" className="bg-paper">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 md:flex-row md:items-center">
            <h2
              id="notices"
              className="shrink-0 rounded-btn bg-brand2 px-4 py-2 text-sm font-bold uppercase text-white"
            >
              Important notices
            </h2>
            <ul className="flex flex-1 flex-col gap-2 text-sm md:flex-row md:gap-6">
              {NOTICES.map(([d, t]) => (
                <li key={t}>
                  <span className="font-semibold text-brand">{d}</span> — {t}
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* programmes */}
        <section id="programmes" className="mx-auto max-w-6xl px-4 py-16">
          <h2 className="text-3xl font-extrabold text-accent">Academic Programmes</h2>
          <p className="mt-2 text-muted">
            Undergraduate, postgraduate and doctoral programmes across faculties.
          </p>
          <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {PROGRAMMES.map(([name, meta, faculty]) => (
              <article key={name} className="card overflow-hidden">
                <div className="brand-gradient h-2" />
                <div className="p-5">
                  <p className="text-xs font-semibold uppercase tracking-wider text-brand2">{faculty}</p>
                  <h3 className="mt-1 text-lg font-bold">{name}</h3>
                  <p className="mt-1 text-sm text-muted">{meta}</p>
                  <button
                    onClick={openChat}
                    className="mt-4 text-sm font-semibold text-brand hover:underline"
                  >
                    Ask about this programme →
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        {/* numbers */}
        <section className="bg-accent text-white">
          <div className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-4 py-12 text-center md:grid-cols-4">
            {[
              ["120+", "Classrooms"],
              ["90+", "Laboratories"],
              ["30+", "Sports awards"],
              ["200+", "Recruiters"],
            ].map(([n, l]) => (
              <div key={l}>
                <p className="text-4xl font-extrabold">{n}</p>
                <p className="mt-1 text-sm opacity-90">{l}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="bg-[#212121] text-sm text-gray-300">
        <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 md:grid-cols-4">
          <div>
            <p className="font-bold text-white">Demo College of Engineering</p>
            <p className="mt-2">1 Example Road, Sample City 000000</p>
            <p className="mt-1">admissions@example.com</p>
          </div>
          {[
            ["Admission", ADMISSIONS.slice(0, 5)],
            ["Academics", ["Faculties", "Programmes", "Examinations", "Library"]],
            ["Student Life", ["Hostel", "Sports", "Clubs", "Placements"]],
          ].map(([h, items]) => (
            <div key={h as string}>
              <p className="font-bold text-white">{h}</p>
              <ul className="mt-2 space-y-1">
                {(items as string[]).map((i) => (
                  <li key={i}>
                    <a href="#" className="hover:text-white">
                      {i}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="border-t border-white/10 py-4 text-center text-xs">
          Demo site for AdmitAI development · fictional college
        </p>
      </footer>

      <Script
        src="/embed.js"
        data-key="pk_demo_college_0001"
        data-color="#E34A21"
        strategy="afterInteractive"
      />
    </div>
  );
}
