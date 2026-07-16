// Shared color/size scales for the heat-flow visualization (SPEC §8).
//
// Principle carried over from the blueprint: **the unknown is styled as
// unknown** — an element without a measurement renders in a dedicated dim
// grey, never in a color the ramps could produce for a healthy reading.

/** Color for an element with no measurement — deliberately dim/neutral. */
export const UNOBSERVED = "#39424f";
export const UNOBSERVED_LINE = "#2b323c";
/** Dash pattern for unobserved polylines (grey alone can read as "cold"). */
export const UNOBSERVED_DASH = "6 6";

/** Substation minimum differential pressure [bar] — display anchor for the
 *  Δp layer (mirrors the backend default RTHEATFLOW_DP_MIN_BAR). */
export const DP_MIN_BAR = 0.5;

/** Velocity warning anchor [m/s] (SPEC §8: capacity teaching, ~1.5–3). */
export const V_WARN = 1.5;
/** Velocity scale end [m/s] — full red. */
export const V_MAX = 3.0;

/** Supply ramp low anchor [°C]: below this everything reads "cold end". */
export const T_SUPPLY_LOW = 60;
/** Return ramp domain [°C] (SPEC §8: cool ramp 25–70). */
export const T_RETURN_LOW = 25;
export const T_RETURN_HIGH = 70;

type Stop = [number, [number, number, number]];

function ramp(stops: Stop[], t: number): string {
  const x = Math.min(1, Math.max(0, t));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const f = t1 === t0 ? 0 : (x - t0) / (t1 - t0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * f));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  const last = stops[stops.length - 1][1];
  return `rgb(${last[0]},${last[1]},${last[2]})`;
}

// Supply temperature: warm YlOrRd ramp. Domain-anchored — full-hot exactly
// at the active heating curve's t_flow_design (SPEC §8), so a 4G 70 °C net
// glows just as a 3G 110 °C net does at ITS design temperature.
const SUPPLY: Stop[] = [
  [0.0, [254, 235, 150]],
  [0.35, [254, 178, 76]],
  [0.7, [252, 78, 42]],
  [1.0, [177, 0, 38]],
];

// Return temperature: cool blue → violet/magenta. A hot return (low-ΔT
// syndrome) pops in violet — clearly distinct from the warm supply ramp.
const RETURN: Stop[] = [
  [0.0, [147, 197, 253]],
  [0.5, [59, 130, 246]],
  [0.75, [124, 58, 237]],
  [1.0, [217, 70, 239]],
];

// Velocity: green (idle) … amber at the V_WARN anchor … red at V_MAX.
const VELOCITY: Stop[] = [
  [0.0, [34, 197, 94]],
  [V_WARN / V_MAX, [245, 158, 11]],
  [0.75, [249, 115, 22]],
  [1.0, [239, 68, 68]],
];

/** Supply temperature [°C] -> warm ramp anchored at *tFlowDesign*. */
export function supplyTempColor(
  t: number | null | undefined,
  tFlowDesign: number,
): string {
  if (t == null) return UNOBSERVED;
  const span = Math.max(1e-9, tFlowDesign - T_SUPPLY_LOW);
  return ramp(SUPPLY, (t - T_SUPPLY_LOW) / span);
}

/** Return temperature [°C] -> cool ramp over 25…70 °C. */
export function returnTempColor(t: number | null | undefined): string {
  if (t == null) return UNOBSERVED;
  return ramp(RETURN, (t - T_RETURN_LOW) / (T_RETURN_HIGH - T_RETURN_LOW));
}

/** Flow velocity [m/s] -> green…amber(1.5)…red(3). Sign is ignored. */
export function velocityColor(v: number | null | undefined): string {
  if (v == null) return UNOBSERVED;
  return ramp(VELOCITY, Math.abs(v) / V_MAX);
}

/** Consumer Δp [bar] -> traffic light: red below DP_MIN_BAR (starved
 *  substation), amber in the warning margin just above, green when healthy. */
export function dpColor(
  dp: number | null | undefined,
  dpMin: number = DP_MIN_BAR,
): string {
  if (dp == null) return UNOBSERVED;
  if (dp < dpMin) return "#ef4444";
  if (dp < dpMin + 0.15) return "#f59e0b";
  return "#22c55e";
}

/** Map a mass flow to a stroke width, relative to the snapshot's max. */
export function mdotWidth(mdot: number, maxMdot: number): number {
  if (maxMdot <= 0) return 1.5;
  return 1.5 + 4.5 * Math.sqrt(Math.min(1, Math.abs(mdot) / maxMdot));
}

/** Consumer marker radius [px] by design load (sqrt — area ∝ load). */
export function consumerRadius(qDesignW: number): number {
  return Math.min(14, Math.max(4, 4 + 2.2 * Math.sqrt(qDesignW / 10000)));
}

export function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null) return "–";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** CSS linear-gradient string for a colorbar legend of the given colormap. */
function gradientCss(stops: Stop[]): string {
  const parts = stops.map(
    ([t, c]) => `rgb(${c[0]},${c[1]},${c[2]}) ${(t * 100).toFixed(0)}%`,
  );
  return `linear-gradient(to top, ${parts.join(", ")})`;
}

export const SUPPLY_GRADIENT = gradientCss(SUPPLY);
export const RETURN_GRADIENT = gradientCss(RETURN);
export const VELOCITY_GRADIENT = gradientCss(VELOCITY);
