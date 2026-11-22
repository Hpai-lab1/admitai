import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STRINGS } from "@/lib/i18n";
import { CHECKING_MS, OFFER_MS, STILL_MS, Waiting } from "./Waiting";

describe("Waiting", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("says what is happening, then that it is still working, then offers a person", () => {
    const ask = vi.fn();
    const t = STRINGS.en;
    render(<Waiting t={t} onAskSomeone={ask} />);
    expect(screen.queryByRole("status")).toBeNull();
    act(() => vi.advanceTimersByTime(CHECKING_MS));
    expect(screen.getByRole("status").textContent).toContain(t.checking);
    act(() => vi.advanceTimersByTime(STILL_MS - CHECKING_MS));
    expect(screen.getByRole("status").textContent).toContain(t.stillChecking);
    expect(screen.queryByRole("button")).toBeNull();
    act(() => vi.advanceTimersByTime(OFFER_MS - STILL_MS));
    fireEvent.click(screen.getByRole("button", { name: t.askSomeone }));
    expect(ask).toHaveBeenCalledOnce();
  });

  it("speaks the visitor's language", () => {
    render(<Waiting t={STRINGS.kn} onAskSomeone={() => {}} />);
    act(() => vi.advanceTimersByTime(CHECKING_MS));
    expect(screen.getByRole("status").textContent).toContain(STRINGS.kn.checking);
  });
});
