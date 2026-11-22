import { describe, expect, it } from "vitest";
import { duration, feeLabel, money } from "./Programmes";

describe("programme list formatting", () => {
  it("writes amounts the way the college website does", () => {
    expect(money("600000", "INR")).toBe("₹6,00,000");
    expect(money("2000000", "INR")).toBe("₹20,00,000");
    expect(money("3500", "USD")).toBe("USD 3,500");
  });

  it("never invents a period or a duration the website does not give", () => {
    expect(
      feeLabel({
        category: "private",
        component: "total",
        amount: "642700",
        currency: "INR",
        period: "not stated",
      }),
    ).toBe("Private quota · Total fee: ₹6,42,700");
    expect(
      feeLabel({
        category: "general",
        component: "total",
        amount: "600000",
        currency: "INR",
        period: "per year",
      }),
    ).toBe("Total fee: ₹6,00,000 per year");
    expect(duration(null)).toBe("Not stated");
    expect(duration(48)).toBe("4 years");
    expect(duration(54)).toBe("54 months");
  });
});
