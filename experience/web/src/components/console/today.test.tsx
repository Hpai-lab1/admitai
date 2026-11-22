import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TodayView } from "./today";

describe("Needs you today", () => {
  it("lists what to do, each linking to its page, in the order given", () => {
    render(
      <TodayView
        items={[
          {
            kind: "overdue_callbacks",
            count: 2,
            title: "2 callbacks are past their promised time",
            href: "/console",
            priority: "urgent",
          },
          {
            kind: "hot_leads",
            count: 1,
            title: "1 hot lead has not been contacted yet",
            href: "/console/leads?band=hot&stage=new",
            priority: "high",
          },
        ]}
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "2 callbacks are past their promised time",
      "1 hot lead has not been contacted yet",
    ]);
    expect(links[1]).toHaveAttribute("href", "/console/leads?band=hot&stage=new");
    expect(screen.getByText("Urgent")).toBeInTheDocument();
  });

  it("says when nothing is waiting, and when a source could not be loaded", () => {
    const { unmount } = render(<TodayView items={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Nothing is waiting for you");
    unmount();
    render(
      <TodayView
        items={[
          {
            kind: "approvals",
            count: null,
            title: "Approvals could not be loaded right now",
            href: "/console/approvals",
            priority: "normal",
          },
        ]}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("could not be loaded");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
