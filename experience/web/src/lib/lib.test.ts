import { describe, expect, it } from "vitest";
import { errorMessage } from "./api";
import { homeFor, navFor } from "./roles";
import { SseParser } from "./sse";
import { accessible, contrast, hexToTriplet, textOn, themeStyle } from "./theme";
import { slaText } from "./time";
import { type LeadSummary, replyEmail, replyWhatsapp } from "./types";

describe("theme", () => {
  it("turns hex into an rgb triplet", () => {
    expect(hexToTriplet("#E34A21")).toBe("227 74 33");
    expect(hexToTriplet("bad")).toBe("0 0 0");
  });
  it("computes WCAG contrast", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
  });
  it("picks readable text on brand colours", () => {
    expect(textOn("#361C6A")).toBe("#ffffff");
    expect(textOn("#FFEE58")).toBe("#111111");
  });
  it("darkens light brand colours just enough for AA contrast", () => {
    expect(contrast("#E34A21", "#ffffff")).toBeLessThan(4.5);
    const fixed = accessible("#E34A21");
    expect(contrast(fixed, "#ffffff")).toBeGreaterThanOrEqual(4.5);
    expect(contrast(fixed, "#ffffff")).toBeLessThan(5.2); // not darker than needed
    expect(accessible("#361C6A")).toBe("#361C6A"); // already fine: unchanged
  });
  it("sets CSS variables, falling back to defaults", () => {
    const s = themeStyle({ primary_color: "#E34A21", font: "montserrat" }) as Record<string, string>;
    expect(s["--brand"]).toBe(hexToTriplet(accessible("#E34A21")));
    expect(s["--brand-font"]).toContain("--font-montserrat");
    expect(s["--accent"]).toBe(hexToTriplet("#111827"));
  });
});

describe("SseParser", () => {
  it("handles events split across chunks", () => {
    const p = new SseParser();
    expect(p.push('event: token\ndata: {"text":"Hel')).toEqual([]);
    expect(p.push('lo"}\n\nevent: done\ndata: {}\n\n')).toEqual([
      { event: "token", data: '{"text":"Hello"}' },
      { event: "done", data: "{}" },
    ]);
  });
  it("handles CRLF line endings", () => {
    expect(new SseParser().push("data: x\r\n\r\n")).toEqual([{ event: "message", data: "x" }]);
  });
});

describe("roles", () => {
  it("shows marketing no queue or admin", () => {
    const labels = navFor("marketing_manager").map((n) => n.label);
    expect(labels).not.toContain("Queue");
    expect(labels).not.toContain("Admin");
    expect(homeFor("marketing_manager")).toBe("/console/leads");
  });
  it("shows faculty the queue but not leads", () => {
    const labels = navFor("faculty").map((n) => n.label);
    expect(labels).toContain("Queue");
    expect(labels).not.toContain("Leads");
  });
  it("gives college admins everything", () => {
    expect(navFor("college_admin").map((n) => n.label)).toContain("Admin");
  });
});

describe("slaText", () => {
  const now = new Date("2026-10-27T10:00:00Z");
  it("counts down and flags overdue", () => {
    expect(slaText("2026-10-27T13:00:00Z", now)).toEqual({ text: "due in 3 h", overdue: false });
    expect(slaText("2026-10-27T09:30:00Z", now)).toEqual({ text: "overdue by 30 min", overdue: true });
  });
});

describe("errorMessage", () => {
  it("reads FastAPI error shapes", () => {
    expect(errorMessage({ detail: "Nope" }, "x")).toBe("Nope");
    expect(errorMessage({ detail: [{ msg: "Value error, Enter an e-mail" }] }, "x")).toBe("Enter an e-mail");
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });
});

describe("i18n helpers", async () => {
  const { detectLang, isRephrase, STRINGS } = await import("./i18n");
  it("detects Hindi, Kannada and English", () => {
    expect(detectLang("छात्रावास है?")).toBe("hi");
    expect(detectLang("ಹಾಸ್ಟೆಲ್ ಇದೆಯೇ")).toBe("kn");
    expect(detectLang("Is there a hostel?")).toBe("en");
  });
  it("spots a rephrased repeat but not a new question", () => {
    expect(isRephrase("What is the B.Tech fee?", "what is the fee for B.Tech")).toBe(true);
    expect(isRephrase("What is the B.Tech fee?", "Is there a hostel?")).toBe(false);
  });
  it("has every English key in Hindi", () => {
    expect(Object.keys(STRINGS.hi).sort()).toEqual(Object.keys(STRINGS.en).sort());
  });
});

describe("onDark", async () => {
  const { onDark, contrast } = await import("./theme");
  it("lightens dark brand colours so text reads on the dark surface", () => {
    expect(contrast(onDark("#361C6A"), "#1E1E24")).toBeGreaterThanOrEqual(4.5);
  });
});

describe("replyEmail", () => {
  const lead = { email: "a@example.com", is_minor: false, guardian_contact: null } as LeadSummary;
  it("uses the applicant's e-mail, or for a minor only the guardian's verified e-mail", () => {
    expect(replyEmail(lead)).toBe("a@example.com");
    expect(replyEmail({ ...lead, email: null })).toBeNull();
    expect(
      replyEmail({
        ...lead,
        is_minor: true,
        guardian_contact: { channel: "email", destination: "g@example.com" },
      }),
    ).toBe("g@example.com");
    expect(
      replyEmail({
        ...lead,
        is_minor: true,
        guardian_contact: { channel: "whatsapp", destination: "+15550100" },
      }),
    ).toBeNull();
    expect(replyEmail(null)).toBeNull();
  });
});

describe("replyWhatsapp", () => {
  const lead = { phone: "+15550100046", is_minor: false, guardian_contact: null } as LeadSummary;
  it("uses the adult's own number, or for a minor only the guardian's WhatsApp number", () => {
    expect(replyWhatsapp(lead)).toBe("+15550100046");
    expect(replyWhatsapp({ ...lead, phone: null })).toBeNull();
    const minor = { ...lead, phone: null, is_minor: true };
    expect(
      replyWhatsapp({
        ...minor,
        guardian_contact: { channel: "whatsapp", destination: "+15550100041" },
      }),
    ).toBe("+15550100041");
    expect(
      replyWhatsapp({ ...minor, guardian_contact: { channel: "email", destination: "g@example.com" } }),
    ).toBeNull();
    expect(replyWhatsapp(null)).toBeNull();
  });
});
