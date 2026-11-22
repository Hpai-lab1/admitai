import { describe, expect, it } from "vitest";
import { nextFeedback } from "./feedback";

describe("nextFeedback", () => {
  it("sends a thumbs-up at once", () => expect(nextFeedback(1)).toBe("sent"));
  it("asks what was wrong after a thumbs-down", () => expect(nextFeedback(-1)).toBe("down"));
  it("offers an optional box after wrong, outdated or incomplete", () => {
    for (const r of ["wrong", "outdated", "incomplete"] as const) expect(nextFeedback(-1, r)).toBe("detail");
  });
  it("sends the other reasons at once, as before", () => {
    expect(nextFeedback(-1, "not_what_i_asked")).toBe("sent");
    expect(nextFeedback(-1, "hard_to_understand")).toBe("sent");
  });
  it("still needs the text for 'other'", () => expect(nextFeedback(-1, "other")).toBe("other"));
  it("sends once the box is filled in or skipped", () => {
    expect(nextFeedback(-1, "wrong", "The fee is 2,75,000 now")).toBe("sent");
    expect(nextFeedback(-1, "outdated", "")).toBe("sent");
  });
});

import { chatRatingFeedback, defaultChoice } from "./feedback";

describe("chat rating dropdown", () => {
  it("preselects satisfactory for 4 or 5 stars only", () => {
    expect(defaultChoice(5)).toBe("satisfied");
    expect(defaultChoice(4)).toBe("satisfied");
    expect(defaultChoice(2)).toBe("");
  });
  it("sends no reason when satisfied", () =>
    expect(chatRatingFeedback("satisfied", "")).toEqual({ reason: null, comment: null }));
  it("needs words for 'other'", () => {
    expect(chatRatingFeedback("other", "  ")).toBeNull();
    expect(chatRatingFeedback("other", "Too slow")).toEqual({ reason: "other", comment: "Too slow" });
  });
  it("keeps 'not satisfactory' readable for staff", () => {
    expect(chatRatingFeedback("not_satisfied", "")).toEqual({ reason: "other", comment: "Not satisfactory" });
    expect(chatRatingFeedback("not_satisfied", "no fees")).toEqual({
      reason: "other",
      comment: "Not satisfactory: no fees",
    });
  });
  it("passes the API's own reasons through", () =>
    expect(chatRatingFeedback("outdated", "2025 fees")).toEqual({
      reason: "outdated",
      comment: "2025 fees",
    }));
});
