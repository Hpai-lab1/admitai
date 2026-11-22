import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRefreshWhile } from "./ui";

describe("useRefreshWhile", () => {
  afterEach(() => vi.useRealTimers());

  it("reloads every 20 seconds only while something is being tested", () => {
    vi.useFakeTimers();
    const reload = vi.fn(async () => {});
    const { rerender } = renderHook(({ on }) => useRefreshWhile(on, reload), { initialProps: { on: true } });
    vi.advanceTimersByTime(40000);
    expect(reload).toHaveBeenCalledTimes(2);
    rerender({ on: false });
    vi.advanceTimersByTime(60000);
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
