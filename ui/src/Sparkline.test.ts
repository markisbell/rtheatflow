// Sparkline helper tests — the blueprint's distinctive regression: the
// marker (rating/setpoint line) must extend the AXIS, never the DATA (a
// marker pushed into the series would draw a phantom peak at the end).
import { describe, expect, it } from "vitest";
import { axisTicks, chartExtent } from "./components/Sparkline";

describe("chartExtent", () => {
  it("covers the series", () => {
    const e = chartExtent([1, 5, 3], null);
    expect(e.max).toBe(5);
    expect(e.min).toBe(0); // zero baseline always in view
  });

  it("keeps negative values in view (net feed-in)", () => {
    const e = chartExtent([2, -4, 1], null);
    expect(e.min).toBe(-4);
    expect(e.max).toBe(2);
  });

  it("includes the overlay series", () => {
    const e = chartExtent([1, 2], [7, 0], undefined);
    expect(e.max).toBe(7);
  });

  it("gives the marker headroom WITHOUT mutating the series", () => {
    const main = [1, 2, 3];
    const e = chartExtent(main, null, 10);
    expect(e.max).toBeCloseTo(10.5); // marker * 1.05 in view
    expect(main).toEqual([1, 2, 3]); // pure — no phantom peak
  });

  it("ignores a zero/absent marker", () => {
    expect(chartExtent([1, 2], null, 0).max).toBe(2);
    expect(chartExtent([1, 2], null).max).toBe(2);
  });
});

describe("axisTicks", () => {
  it("spans min..max inclusive, evenly", () => {
    expect(axisTicks(0, 100)).toEqual([0, 25, 50, 75, 100]);
  });

  it("handles negative ranges", () => {
    const t = axisTicks(-10, 10, 3);
    expect(t).toEqual([-10, 0, 10]);
  });
});
