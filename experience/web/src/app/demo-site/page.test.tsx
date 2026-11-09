import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DemoSite from "./page";

describe("demo college site", () => {
  it("keeps its own light colours when the visitor's system is in dark mode", () => {
    render(<DemoSite />);
    // globals.css scopes the light palette to `.light`; without it the menu turns light-on-white
    const page = screen.getByRole("navigation", { name: "Main" }).closest(".light");
    expect(page).not.toBeNull();
    expect(page).toHaveClass("bg-surface", "text-ink");
  });
});
