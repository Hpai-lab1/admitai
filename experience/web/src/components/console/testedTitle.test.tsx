import { describe, expect, it } from "vitest";
import { testedTitle } from "./Shell";

describe("bell text for a finished test", () => {
  it("says whether the change is live, taken down or unchanged", () => {
    const t = "Expert correction: B.Tech CSE fee";
    expect(testedTitle({ payload: { title: t, passed: true } })).toBe("✅ Live now: B.Tech CSE fee");
    expect(testedTitle({ payload: { title: t, passed: false, taken_down: true } })).toBe(
      "❌ Failed the test and taken down: B.Tech CSE fee",
    );
    expect(testedTitle({ payload: { title: t, passed: false } })).toBe(
      "❌ Failed the test, nothing changed: B.Tech CSE fee",
    );
  });
});
