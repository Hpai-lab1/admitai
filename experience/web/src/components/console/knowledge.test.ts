import { describe, expect, it } from "vitest";
import { filterQa, statusLabel, statusTone } from "./knowledge";

const qa = [
  { id: "1", question: "What is the hostel fee?", answer: "90000", status: "approved" },
  { id: "2", question: "Tuition fee for CSE?", answer: "250000", status: "suspended" },
  { id: "3", question: "Is there an ATM?", answer: "Yes", status: "expired" },
];

describe("knowledge filters", () => {
  it("shows answers in use by default, the rest on request, and searches both", () => {
    expect(filterQa(qa, "", "live").map((a) => a.id)).toEqual(["1"]);
    expect(filterQa(qa, "", "other").map((a) => a.id)).toEqual(["2", "3"]);
    expect(filterQa(qa, "fee", "all").map((a) => a.id)).toEqual(["1", "2"]);
  });
  it("names statuses for staff and colours them by what they mean", () => {
    expect(statusLabel("approved")).toBe("In use");
    expect(statusLabel("suspended")).toBe("Paused");
    expect(statusTone("approved")).toBe("green");
    expect(statusTone("expired")).toBe("gray");
  });
});
