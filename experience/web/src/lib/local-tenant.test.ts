import { describe, expect, it } from "vitest";
import type { TenantInfo } from "./widget-api";
import { localTenant } from "./local-tenant";

const tenant = {
  assistant_name: "Admissions Assistant",
  greeting: "Hello!",
  announcement: "Admissions open for 2026",
  starter_questions: ["What is the fee for B.Tech CSE?"],
  topics: ["UG Programmes"],
  translations: {
    kn: {
      assistant_name: "ಪ್ರವೇಶ ಸಹಾಯಕ",
      greeting: "ನಮಸ್ಕಾರ!",
      starter_questions: ["ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಎಷ್ಟು?"],
    },
  },
} as unknown as TenantInfo;

describe("the college's texts in the visitor's language", () => {
  it("are unchanged in English", () => {
    expect(localTenant(tenant, "en").starter_questions).toEqual(["What is the fee for B.Tech CSE?"]);
  });

  it("use the college's Kannada texts, and never show an English one in Kannada", () => {
    const kn = localTenant(tenant, "kn");
    expect(kn.assistant_name).toBe("ಪ್ರವೇಶ ಸಹಾಯಕ");
    expect(kn.starter_questions).toEqual(["ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಎಷ್ಟು?"]);
    expect(kn.announcement).toBeNull(); // not translated: left out, not shown in English
    expect(kn.topics).toEqual([]);
  });

  it("fall back to the widget's own name and greeting when a college has none", () => {
    const hi = localTenant(tenant, "hi");
    expect(hi.assistant_name).toBe("प्रवेश सहायक");
    expect(hi.greeting).toMatch(/नमस्ते/);
    const branded = localTenant({ ...tenant, assistant_name: "Ask Riya" } as TenantInfo, "hi");
    expect(branded.assistant_name).toBe("Ask Riya"); // a brand name is kept
  });
});
