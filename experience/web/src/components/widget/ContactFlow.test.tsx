import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { hasContactConsent, type ConsentRecord, type TenantInfo } from "@/lib/widget-api";
import { ContactFlow } from "./ContactFlow";

const tenant = {
  id: "t",
  name: "Demo College",
  assistant_name: "A",
  greeting: "Hi",
  starter_questions: [],
  topics: ["UG Programmes"],
  departments: [],
  consent_text_version: "v1",
  privacy_notice_url: null,
  office_hours: "",
  response_time_hours: 24,
  apply_url: null,
  announcement: null,
  primary_color: "#E34A21",
  secondary_color: "#992361",
  accent_color: "#361C6A",
  font: "system",
} satisfies TenantInfo;

describe("ContactFlow", () => {
  it("needs the contact box ticked before continuing", () => {
    render(
      <ContactFlow
        intent="callback"
        token="t"
        tenant={tenant}
        conversationId="c"
        transcript={[]}
        onClose={vi.fn()}
      />,
    );
    const go = screen.getByRole("button", { name: "Agree and continue" });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/may contact me/i));
    expect(go).toBeEnabled();
  });

  it("records consent, then asks for age; under-18s are asked for a parent's contact", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ id: "x" }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ContactFlow
        intent="callback"
        token="t"
        tenant={tenant}
        conversationId="c"
        transcript={[]}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText(/may contact me/i));
    fireEvent.click(screen.getByRole("button", { name: "Agree and continue" }));
    fireEvent.click(await screen.findByRole("button", { name: /under 18/i }));
    expect(screen.getByLabelText(/parent's or guardian's e-mail/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/mobile number/i)).not.toBeInTheDocument();
    const bodies = fetchMock.mock.calls
      .map((c) => ((c as unknown[])[1] as RequestInit | undefined)?.body)
      .filter(Boolean) // the GET of earlier consents has no body
      .map((b) => JSON.parse(String(b)));
    expect(bodies[0]).toMatchObject({ purpose: "contact", granted: true, text_version: "v1" });
    vi.unstubAllGlobals();
  });
});

describe("ContactFlow in Hindi", () => {
  it("shows the consent text in Hindi", () => {
    render(
      <ContactFlow
        intent="enquiry"
        token="t"
        tenant={tenant}
        lang="hi"
        conversationId="c"
        transcript={[]}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "सहमत हूँ, आगे बढ़ें" })).toBeDisabled();
    expect(screen.getByText(/प्रवेश के बारे में आपसे संपर्क/)).toBeInTheDocument();
  });
});

describe("ContactFlow with Module B's signals", () => {
  it("goes straight to the guardian when Module B detected a minor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 201, headers: { "content-type": "application/json" } })),
    );
    render(
      <ContactFlow
        intent="enquiry"
        token="t"
        tenant={tenant}
        conversationId="c"
        transcript={[]}
        knownMinor
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText(/may contact me/i));
    fireEvent.click(screen.getByRole("button", { name: "Agree and continue" }));
    expect(await screen.findByLabelText(/parent's or guardian's e-mail/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /under 18/i })).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe("ContactFlow in the same chat again", () => {
  const yes = (over: Partial<ConsentRecord> = {}): ConsentRecord => ({
    purpose: "contact",
    granted: true,
    given_by: "self",
    text_version: "v1",
    captured_at: "2026-11-08T10:00:00Z",
    withdrawn_at: null,
    ...over,
  });

  it("reuses only a current, own, unwithdrawn yes to the contact consent", () => {
    expect(hasContactConsent([yes()], "v1")).toBe(true);
    expect(hasContactConsent([yes()], "v2")).toBe(false); // the wording changed: ask again
    expect(hasContactConsent([yes({ withdrawn_at: "2026-11-08T11:00:00Z" })], "v1")).toBe(false);
    expect(hasContactConsent([yes({ purpose: "marketing" })], "v1")).toBe(false);
    expect(hasContactConsent([yes({ given_by: "guardian" })], "v1")).toBe(false);
    expect(
      hasContactConsent([yes(), yes({ granted: false, captured_at: "2026-11-08T12:00:00Z" })], "v1"),
    ).toBe(false); // the latest answer counts
    expect(hasContactConsent([], "v1")).toBe(false);
  });

  it("skips consent and age when the visitor already answered both in this chat", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify([yes()]), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    render(
      <ContactFlow
        intent="enquiry"
        token="t"
        tenant={tenant}
        conversationId="c"
        transcript={[]}
        knownAdult
        onClose={vi.fn()}
      />,
    );
    expect(await screen.findByText(/already agreed earlier in this chat/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agree and continue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /under 18/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/mobile number/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
