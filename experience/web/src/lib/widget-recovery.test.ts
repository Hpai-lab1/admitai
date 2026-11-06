import { afterEach, describe, expect, it, vi } from "vitest";
import { ask, isLong, quickReplies, recoverAnswer, StreamCut } from "./widget-api";

afterEach(() => vi.unstubAllGlobals());

const answer = (question: string) =>
  new Response(
    JSON.stringify({
      question,
      text: "The fee is 250000 INR [1].",
      message_id: "m1",
      trace_id: "t1",
      sources: [{ title: "Fees" }],
      offer_handoff: false,
    }),
    { status: 200 },
  );

describe("dropped answers (PRD F1 error handling)", () => {
  it("reports a cut stream as StreamCut, not as an ordinary error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(ask("c1", "fees?", "tok", () => {})).rejects.toBeInstanceOf(StreamCut);
    const body = new ReadableStream({ start: (c) => c.close() }); // ends with no "done" event
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    await expect(ask("c1", "fees?", "tok", () => {})).rejects.toBeInstanceOf(StreamCut);
  });

  it("waits until Module B has the answer to this question, then returns it", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 404 })) // still being written
      .mockResolvedValueOnce(answer("an earlier question"))
      .mockResolvedValueOnce(answer("What is the fee?"));
    vi.stubGlobal("fetch", fetch);
    const got = await recoverAnswer("c1", "What is the fee?", "tok", { waitMs: 0 });
    expect(got?.message_id).toBe("m1");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("gives up honestly when the answer never appears", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    expect(await recoverAnswer("c1", "q", "tok", { tries: 3, waitMs: 0 })).toBeNull();
  });
});

describe("quick replies and long answers", () => {
  it("suggests only what was not asked yet, at most three", () => {
    expect(quickReplies(["Fees?", "Hostel?", "Dates?", "Scholarships?"], [" fees? "])).toEqual([
      "Hostel?",
      "Dates?",
      "Scholarships?",
    ]);
  });
  it("collapses answers longer than 12 lines", () => {
    expect(isLong("short answer")).toBe(false);
    expect(isLong("x".repeat(45 * 12))).toBe(false);
    expect(isLong("x".repeat(45 * 12 + 1))).toBe(true);
    expect(isLong(Array(13).fill("line").join("\n"))).toBe(true);
  });
});
