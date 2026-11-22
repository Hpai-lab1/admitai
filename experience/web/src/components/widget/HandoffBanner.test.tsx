import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { STRINGS } from "@/lib/i18n";
import { handoffStatus } from "@/lib/widget-api";
import { HandoffBanner, bannerText } from "./HandoffBanner";

// No photo here: the banner shows the name alone (the photo is tested in the API tests).
vi.mock("@/lib/widget-api", () => ({ handoffStatus: vi.fn(), handoffPhoto: vi.fn(async () => null) }));
const mocked = vi.mocked(handoffStatus);

const t = STRINGS.en;

describe("handoff banner", () => {
  it("says who it is connecting to, then who claimed it", () => {
    expect(bannerText({ status: "open", queue: "admission" }, t)).toBe("Connecting you to Admissions…");
    expect(bannerText({ status: "open", queue: "faculty", department: "Civil" }, t)).toBe(
      "Connecting you to Civil…",
    );
    expect(bannerText({ status: "claimed", staff_first_name: "Asha" }, t)).toBe(
      "Asha from Admissions is looking after your request.",
    );
  });

  it("shows nothing without a live request", () => {
    expect(bannerText(null, t)).toBeNull();
    expect(bannerText({ status: "none" }, t)).toBeNull();
    expect(bannerText({ status: "resolved", staff_first_name: "Asha" }, t)).toBeNull();
  });

  it("never invents a name", () => {
    expect(bannerText({ status: "claimed", staff_first_name: null }, t)).toBe(
      "Connecting you to Admissions…",
    );
  });

  describe("asking the server", () => {
    afterEach(() => {
      vi.useRealTimers();
      mocked.mockReset();
    });
    const tick = () => act(() => vi.advanceTimersByTimeAsync(15000));

    it("changes when someone claims the request, then stops once resolved", async () => {
      vi.useFakeTimers();
      mocked.mockResolvedValueOnce({ status: "open" });
      render(<HandoffBanner token="t" conversationId="c" t={t} />);
      await act(() => vi.advanceTimersByTimeAsync(0));
      expect(screen.getByRole("status")).toHaveTextContent("Connecting you to Admissions…");
      mocked.mockResolvedValueOnce({ status: "claimed", staff_first_name: "Asha" });
      await tick();
      expect(screen.getByRole("status")).toHaveTextContent("Asha from Admissions");
      mocked.mockResolvedValueOnce({ status: "resolved" });
      await tick();
      expect(screen.queryByRole("status")).toBeNull();
      const calls = mocked.mock.calls.length;
      await tick();
      expect(mocked.mock.calls.length).toBe(calls);
    });

    it("stops asking when the session is refused, but keeps trying after a network failure", async () => {
      vi.useFakeTimers();
      mocked.mockRejectedValueOnce(new ApiError(0, "offline"));
      render(<HandoffBanner token="t" conversationId="c" t={t} />);
      await act(() => vi.advanceTimersByTimeAsync(0));
      mocked.mockRejectedValueOnce(new ApiError(401, "expired"));
      await tick();
      const calls = mocked.mock.calls.length;
      expect(calls).toBe(2);
      await tick();
      expect(mocked.mock.calls.length).toBe(calls);
    });
  });
});
