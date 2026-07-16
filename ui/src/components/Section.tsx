import type { ReactNode } from "react";

/** A collapsible side-panel section: chevron + title + badges (blueprint
 *  port). The body renders only while open, so heavy children load lazily. */
export default function Section({ title, badges = [], open, onToggle, children }: {
  title: string;
  badges?: string[];
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className="section">
      <div className="sec-head" onClick={onToggle}>
        <span className={`chev${open ? " open" : ""}`}>▸</span>
        <span className="sec-title">{title}</span>
        <span className="sec-badges">{badges.map((b, i) => <span key={i}>{b}</span>)}</span>
      </div>
      {open && <div className="sec-body">{children}</div>}
    </div>
  );
}

/** One label/value line in a section (blueprint `Stat`). */
export function Stat({ label, value, color }: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="stat-row">
      <span className="muted">{label}</span>
      <span className="v" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}
