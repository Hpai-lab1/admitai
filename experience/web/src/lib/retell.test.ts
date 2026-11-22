import { describe, expect, it } from "vitest";
import { allLines, latestLine } from "./retell";

describe("Retell live transcript", () => {
  it("shows the latest thing said", () => {
    expect(
      latestLine([
        { role: "agent", content: "Hello!" },
        { role: "user", content: " What is the application fee? " },
      ]),
    ).toEqual({ role: "user", content: "What is the application fee?" });
  });
  it("ignores empty lines and odd input", () => {
    expect(
      latestLine([
        { role: "agent", content: "Hi" },
        { role: "user", content: "  " },
      ]),
    ).toEqual({
      role: "agent",
      content: "Hi",
    });
    expect(latestLine(undefined)).toBeNull();
    expect(latestLine([])).toBeNull();
  });
});

describe("allLines", () => {
  it("keeps every line of the call, in order, for the chat", () => {
    expect(
      allLines([
        { role: "agent", content: "Hello! Ask me about admissions." },
        { role: "user", content: " What is the fee? " },
        { role: "agent", content: "" },
        { role: "agent", content: "The fee is two lakh rupees a year." },
      ]),
    ).toEqual([
      { role: "agent", content: "Hello! Ask me about admissions." },
      { role: "user", content: "What is the fee?" },
      { role: "agent", content: "The fee is two lakh rupees a year." },
    ]);
    expect(allLines(undefined)).toEqual([]);
  });
});
