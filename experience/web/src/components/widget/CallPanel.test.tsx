import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { STRINGS } from "@/lib/i18n";
import { CallPanel } from "./Widget";

const t = STRINGS.en;

describe("Retell voice call panel", () => {
  it("says it is connecting, then listening, so the visitor knows to just talk", () => {
    const { rerender } = render(
      <CallPanel call={{ status: "connecting", heard: "", said: "" }} t={t} onEnd={() => {}} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Connecting the voice call");
    rerender(<CallPanel call={{ status: "listening", heard: "", said: "" }} t={t} onEnd={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Listening — go ahead");
    expect(screen.getByText(/no need to press anything/)).toBeInTheDocument();
  });

  it("shows what was heard and what the assistant is saying", () => {
    render(
      <CallPanel
        call={{
          status: "speaking",
          heard: "What is the application fee?",
          said: "It is Rs. 2000 per program.",
        }}
        t={t}
        onEnd={() => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Assistant is speaking");
    expect(screen.getByText("What is the application fee?")).toBeInTheDocument();
    expect(screen.getByText("It is Rs. 2000 per program.")).toBeInTheDocument();
  });

  it("ends only through its own End call button", () => {
    const onEnd = vi.fn();
    render(<CallPanel call={{ status: "listening", heard: "", said: "" }} t={t} onEnd={onEnd} />);
    fireEvent.click(screen.getByRole("button", { name: "End call" }));
    expect(onEnd).toHaveBeenCalledOnce();
  });
});
