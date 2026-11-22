import { describe, expect, it } from "vitest";
import { sortQueue } from "./queue";
import type { Handoff } from "@/lib/types";

const h = (id: string, due: string, created = "2026-11-01T00:00:00Z") =>
  ({ id, sla_due_at: due, created_at: created }) as Handoff;

describe("sortQueue", () => {
  it("puts the most overdue first, then the soonest due", () => {
    const items = [
      h("later", "2026-11-12T10:00:00Z"),
      h("overdue", "2026-11-09T10:00:00Z"),
      h("soon", "2026-11-11T10:00:00Z"),
    ];
    expect(sortQueue(items, "active").map((x) => x.id)).toEqual(["overdue", "soon", "later"]);
  });
  it("shows the newest resolved first", () => {
    const items = [h("old", "x", "2026-11-01T00:00:00Z"), h("new", "x", "2026-11-05T00:00:00Z")];
    expect(sortQueue(items, "resolved").map((x) => x.id)).toEqual(["new", "old"]);
  });
});
