import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnswerText } from "./Widget";

const sources = [
  { title: "Program catalogue 2027-28", academic_year: "2027-28" },
  { title: "Hostel rules 2027-28", heading_path: "Hostel rules > Timings" },
];

describe("AnswerText", () => {
  it("turns inline [n] markers into citation chips in place", () => {
    const { container } = render(
      <AnswerText text="The fee is 250000 INR [1]. Gates close at 21:30 [2]." sources={sources} />,
    );
    expect(container.textContent).toBe("The fee is 250000 INR1. Gates close at 21:302.");
    expect(
      screen.getByRole("button", { name: /Source 2: Hostel rules 2027-28 > Timings/ }),
    ).toBeInTheDocument();
  });

  it("puts chips at the end when there are no markers", () => {
    render(<AnswerText text="Yes, there is a hostel." sources={sources} />);
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("renders light Markdown: bold, italics, code, headings and bullets", () => {
    const { container } = render(
      <AnswerText
        text={"## Fees\n- **Tuition**: 250000 INR [1]\n* Hostel is *optional*; pay by `NEFT`"}
        sources={sources}
      />,
    );
    expect(container.querySelectorAll("strong")).toHaveLength(2); // the heading and "Tuition"
    expect(container.querySelector("em")?.textContent).toBe("optional");
    expect(container.querySelector("code")?.textContent).toBe("NEFT");
    expect(container.textContent).toContain("• Tuition: 250000 INR");
    expect(container.textContent).not.toContain("**");
  });

  it("never renders HTML from an answer and leaves word-internal underscores alone", () => {
    const { container } = render(
      <AnswerText text={'<img src=x onerror="alert(1)"> see B_Tech_CSE and 2*3*4'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe('<img src=x onerror="alert(1)"> see B_Tech_CSE and 2*3*4');
  });
});
