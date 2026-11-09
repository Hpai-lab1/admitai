import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { TenantInfo } from "@/lib/widget-api";
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
    const bodies = fetchMock.mock.calls.map((c) =>
      JSON.parse(String((c as unknown[])[1] && ((c as unknown[])[1] as RequestInit).body)),
    );
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
