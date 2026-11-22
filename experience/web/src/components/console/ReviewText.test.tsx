import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReviewText } from "./ReviewText";

describe("what a rating is about", () => {
  it("shows the visitor's question and the assistant's answer", () => {
    render(
      <ReviewText
        text="found"
        question="Is there an ATM on campus?"
        answer="I don't have that information."
      />,
    );
    expect(screen.getByText("Visitor asked")).toBeInTheDocument();
    expect(screen.getByText("Is there an ATM on campus?")).toBeInTheDocument();
    expect(screen.getByText("I don't have that information.")).toBeInTheDocument();
  });

  it("says why nothing is shown instead of leaving the card empty", () => {
    const { unmount } = render(<ReviewText text="missing" />);
    expect(screen.getByText(/No stored conversation/)).toBeInTheDocument();
    unmount();
    render(<ReviewText text="unavailable" />);
    expect(screen.getByRole("status")).toHaveTextContent("cannot be loaded right now");
  });
});
