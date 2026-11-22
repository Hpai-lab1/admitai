import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { STRINGS } from "@/lib/i18n";
import { Fallback, closestAnswers } from "./Fallback";

const items = [
  {
    question: "Is there a hostel for first-year students?",
    answer: "Yes, separate hostels for men and women.",
  },
  { question: "What is the B.Tech CSE fee?", answer: "250000 INR per year." },
  { question: "Is there an ATM on campus?", answer: "Yes, beside the main gate." },
];

describe("degraded mode", () => {
  it("picks the approved answers closest to the question", () => {
    expect(closestAnswers("hostel fee for first year?", items).map((x) => x.question)).toEqual([
      "Is there a hostel for first-year students?",
      "What is the B.Tech CSE fee?",
    ]);
    expect(closestAnswers("Is there a swimming pool?", items)).toEqual([]);
  });

  it("shows them under a heading, each opening to its answer", () => {
    render(<Fallback items={items.slice(0, 1)} t={STRINGS.en} />);
    expect(screen.getByText(STRINGS.en.fallbackTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Is there a hostel for first-year students?"));
    expect(screen.getByText("Yes, separate hostels for men and women.")).toBeVisible();
  });
});
