import { describe, expect, it } from "vitest";
import {
  DP_MIN_BAR,
  UNOBSERVED,
  V_WARN,
  consumerRadius,
  dpColor,
  mdotWidth,
  returnTempColor,
  supplyTempColor,
  velocityColor,
} from "./scales";

// The ramps are DOMAIN-ANCHORED (SPEC §8): full-hot exactly at the active
// t_flow_design, warning exactly at the velocity anchor, red exactly below
// DP_MIN_BAR — and the unknown is styled as unknown (dedicated grey that no
// healthy ramp value can produce).

const FULL_HOT = "rgb(177,0,38)";
const COLD_END = "rgb(254,235,150)";

describe("supplyTempColor", () => {
  it("is full-hot exactly at t_flow_design, for ANY design value", () => {
    expect(supplyTempColor(85, 85)).toBe(FULL_HOT); // demo village 3G 85/55
    expect(supplyTempColor(130, 130)).toBe(FULL_HOT); // SPEC example anchor
    expect(supplyTempColor(70, 70)).toBe(FULL_HOT); // 4G network
  });

  it("clamps at both domain ends (60 °C low anchor)", () => {
    expect(supplyTempColor(60, 110)).toBe(COLD_END);
    expect(supplyTempColor(20, 110)).toBe(COLD_END); // below domain -> clamp
    expect(supplyTempColor(140, 110)).toBe(FULL_HOT); // above design -> clamp
  });

  it("moves monotonically warm between the anchors", () => {
    // red channel saturated, green decreasing toward hot
    const g = (c: string) => Number(c.match(/rgb\(\d+,(\d+),\d+\)/)![1]);
    expect(g(supplyTempColor(70, 110))).toBeGreaterThan(g(supplyTempColor(90, 110)));
  });
});

describe("returnTempColor", () => {
  it("spans the 25–70 °C domain with clamping", () => {
    expect(returnTempColor(25)).toBe("rgb(147,197,253)");
    expect(returnTempColor(70)).toBe("rgb(217,70,239)");
    expect(returnTempColor(10)).toBe(returnTempColor(25));
    expect(returnTempColor(90)).toBe(returnTempColor(70));
  });
});

describe("velocityColor", () => {
  it("hits the amber warning anchor exactly at V_WARN", () => {
    expect(velocityColor(V_WARN)).toBe("rgb(245,158,11)");
  });
  it("is green when idle and full red at/beyond 3 m/s", () => {
    expect(velocityColor(0)).toBe("rgb(34,197,94)");
    expect(velocityColor(3)).toBe("rgb(239,68,68)");
    expect(velocityColor(5)).toBe("rgb(239,68,68)");
  });
  it("ignores the flow sign (return pipes run against from->to)", () => {
    expect(velocityColor(-1.5)).toBe(velocityColor(1.5));
  });
});

describe("dpColor", () => {
  it("is red strictly below DP_MIN_BAR, amber in the margin, green above", () => {
    expect(dpColor(DP_MIN_BAR - 0.01)).toBe("#ef4444");
    expect(dpColor(DP_MIN_BAR)).toBe("#f59e0b"); // at the limit: warning
    expect(dpColor(DP_MIN_BAR + 0.2)).toBe("#22c55e");
  });
  it("honors a configurable minimum", () => {
    expect(dpColor(0.6, 0.7)).toBe("#ef4444");
  });
});

describe("unknown-grey principle", () => {
  it("returns the dedicated UNOBSERVED grey for null on every scale", () => {
    expect(supplyTempColor(null, 110)).toBe(UNOBSERVED);
    expect(returnTempColor(null)).toBe(UNOBSERVED);
    expect(velocityColor(null)).toBe(UNOBSERVED);
    expect(dpColor(null)).toBe(UNOBSERVED);
  });

  it("no healthy ramp value can produce the UNOBSERVED grey", () => {
    for (let i = 0; i <= 100; i++) {
      expect(supplyTempColor(50 + i, 110)).not.toBe(UNOBSERVED);
      expect(returnTempColor(20 + i / 2)).not.toBe(UNOBSERVED);
      expect(velocityColor(i / 25)).not.toBe(UNOBSERVED);
    }
  });
});

describe("size scales", () => {
  it("mdotWidth grows monotonically and saturates at the snapshot max", () => {
    expect(mdotWidth(0, 2)).toBeCloseTo(1.5);
    expect(mdotWidth(1, 2)).toBeLessThan(mdotWidth(2, 2));
    expect(mdotWidth(4, 2)).toBe(mdotWidth(2, 2)); // capped
    expect(mdotWidth(1, 0)).toBe(1.5); // degenerate snapshot
  });
  it("consumerRadius is bounded for tiny EFH and big MFH", () => {
    expect(consumerRadius(4000)).toBeGreaterThanOrEqual(4);
    expect(consumerRadius(60000)).toBeLessThanOrEqual(14);
    expect(consumerRadius(60000)).toBeGreaterThan(consumerRadius(8000));
  });
});
