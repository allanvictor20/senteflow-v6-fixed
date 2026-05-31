import { C, s } from "../../utils/theme";

export default function KpiCard({ label, value, color, sub }) {
  return (
    <div style={s.card}>
      <div style={{ color: C.textMuted, fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ color: color || C.white, fontSize: 24, fontWeight: 800, marginBottom: 4 }}>{value}</div>
      {sub && <div style={{ color: C.textMuted, fontSize: 12 }}>{sub}</div>}
    </div>
  );
}