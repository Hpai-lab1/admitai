import { describe, expect, it } from "vitest";
import { meets } from "./targets";

describe("meets", () => {
  it("says whether a rate meets its PRD target, and nothing when there is no data", () => {
    expect(meets(0.53, { min: 0.8, text: "≥ 80%" })).toBe(false);
    expect(meets(0.82, { min: 0.8, text: "≥ 80%" })).toBe(true);
    expect(meets(null, { min: 0.8, text: "≥ 80%" })).toBeNull();
  });
});
