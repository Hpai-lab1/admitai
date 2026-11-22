import { describe, expect, it } from "vitest";
import { forFocus } from "./Widget";

const focus = { title: "B.Des. Fashion Design", greeting: "", suggestions: [] };

describe("questions in a programme's chat", () => {
  it("are asked about that programme when they do not name it", () => {
    expect(forFocus("what is the fee?", focus)).toBe("B.Des. Fashion Design: what is the fee?");
  });
  it("are sent as typed when they already name it, or outside a programme's chat", () => {
    expect(forFocus("What is the fee for B.Des. Fashion Design?", focus)).toBe(
      "What is the fee for B.Des. Fashion Design?",
    );
    expect(forFocus("what is the fee?")).toBe("what is the fee?");
  });
});
