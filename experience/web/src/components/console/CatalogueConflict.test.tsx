import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CatalogueConflict } from "./CatalogueConflict";

describe("CatalogueConflict", () => {
  it("tells staff the correction will not change the fee, and where to change it", () => {
    render(
      <CatalogueConflict
        conflicts={[
          {
            program_id: "p1",
            code: "BTECH-CSE",
            name: "B.Tech Computer Science and Engineering",
            quoted: ["400000"],
            fees: [
              { id: "f1", category: "general", component: "tuition", amount: "250000", period: "per year" },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("4,00,000");
    expect(screen.getByRole("alert")).toHaveTextContent("tuition (general) 2,50,000 per year");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("name it next to the amount");
  });

  it("says which official fee approving changes, without a warning", () => {
    render(
      <CatalogueConflict
        conflicts={[
          {
            program_id: "p1",
            code: "BTECH-CSE",
            name: "B.Tech CSE",
            quoted: ["400000"],
            fees: [
              { id: "f1", category: "general", component: "tuition", amount: "250000", period: "per year" },
            ],
          },
        ]}
        changes={[
          {
            fee_id: "f1",
            program: "B.Tech CSE",
            code: "BTECH-CSE",
            component: "tuition",
            category: "general",
            old: "250000",
            new: "400000",
          },
        ]}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("tuition (general): 2,50,000 → 4,00,000");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows nothing when the correction agrees with the catalogue", () => {
    const { container } = render(<CatalogueConflict conflicts={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
