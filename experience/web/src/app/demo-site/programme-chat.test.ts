import { describe, expect, it } from "vitest";
import { focusFor, suggestionsFor } from "./programme-chat";
import { SAMPLE } from "./programmes.fixture";

const byCode = (c: string) => SAMPLE.find((p) => p.code === c)!;

describe("a programme's own chat", () => {
  it("greets with the programme and offers its most useful questions", () => {
    const f = focusFor(byCode("BDES-FASHION-DESIGN"));
    expect(f.greeting).toContain("I see you're interested in B.Des. Fashion Design");
    expect(f.suggestions).toEqual([
      "What is the fee for B.Des. Fashion Design?",
      "What is the eligibility for B.Des. Fashion Design?",
      "How many seats are there in B.Des. Fashion Design?",
      "How long is B.Des. Fashion Design?",
    ]);
  });

  it("never suggests a question the website cannot answer for that programme", () => {
    for (const p of SAMPLE) {
      const s = suggestionsFor(p);
      expect(s.length).toBeLessThanOrEqual(4);
      if (!p.fees.length) expect(s.some((q) => q.startsWith("What is the fee"))).toBe(false);
      if (!p.eligibility) expect(s.some((q) => q.startsWith("What is the eligibility"))).toBe(false);
      if (!p.intake) expect(s.some((q) => q.startsWith("How many seats"))).toBe(false);
    }
  });
});
