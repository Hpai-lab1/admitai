import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./api";

afterEach(() => vi.unstubAllGlobals());

const ok = () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } });

describe("request", () => {
  it("lets FormData set its own multipart content type (pack uploads)", async () => {
    const fetchMock = vi.fn(async () => ok());
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.append("file", new Blob(["zip"]), "pack.zip");
    await request("/platform/tenants", { method: "POST", body: form }, "tok");
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const upload = calls[0]?.[1].headers as Headers;
    expect(upload.get("Content-Type")).toBeNull();
    expect(upload.get("Authorization")).toBe("Bearer tok");
    await request("/x", { method: "POST", body: "{}" });
    expect((calls[1]?.[1].headers as Headers).get("Content-Type")).toBe("application/json");
  });
});
