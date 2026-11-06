import { describe, expect, it } from "vitest";
import { tooMany } from "./widget-api";

describe("tooMany", () => {
  it("shows the server's message and the wait from Retry-After", async () => {
    const res = new Response(JSON.stringify({ detail: "Too many questions at once." }), {
      status: 429,
      headers: { "Retry-After": "12", "Content-Type": "application/problem+json" },
    });
    expect(await tooMany(res)).toBe("Too many questions at once. (about 12 s)");
  });
  it("falls back when the body is not a problem", async () => {
    expect(await tooMany(new Response("oops", { status: 429 }))).toBe("Too many questions at once.");
  });
});
