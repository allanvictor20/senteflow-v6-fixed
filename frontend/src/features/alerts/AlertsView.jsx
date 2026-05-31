import { C, s } from "../../utils/theme";

export default function AlertsView({ alerts, transactions }) {
  const totalFlagged = alerts.length;

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Alerts</h2>
          <p style={s.pageSubtitle}>
            {totalFlagged} active alert{totalFlagged !== 1 ? "s" : ""} detected
          </p>
        </div>
      </div>

      {alerts.length === 0 ? (
        <div style={{ textAlign: "center", padding: "64px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>✅</div>
          <h3 style={{ color: C.text, margin: "0 0 8px" }}>All clear</h3>
          <p style={{ color: C.textMuted, margin: 0 }}>
            No unusual activity detected in recent transactions.
          </p>
        </div>
      ) : (
        <div style={s.section}>
          {alerts.map((a, i) => (
            <div
              key={a.id || i}
              style={{
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
                padding: "12px 0",
                borderBottom:
                  i < alerts.length - 1 ? `1px solid ${C.border}` : "none",
              }}
            >
              <span
                style={{
                  fontSize: 20,
                  flexShrink: 0,
                  marginTop: 1,
                }}
              >
                {a.type === "suspicious" ? "⚠" : "🚩"}
              </span>
              <div>
                <div
                  style={{
                    color: C.yellow,
                    fontSize: 12,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    marginBottom: 4,
                  }}
                >
                  {a.type === "suspicious" ? "Suspicious Amount" : "Flagged"}
                </div>
                <div style={{ color: C.textDim, fontSize: 13 }}>{a.msg}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Anomaly stats */}
      {transactions.length > 0 && (() => {
        const lowConfidence = transactions.filter(
          (t) => t.confidence_overall === "low"
        );
        return (
          <div style={{ ...s.section, marginTop: 16 }}>
            <h3 style={s.sectionTitle}>Low-Confidence Transactions</h3>
            {lowConfidence.length === 0 ? (
              <p style={{ color: C.textMuted, fontSize: 13, margin: 0 }}>
                No low-confidence transactions.
              </p>
            ) : (
              lowConfidence.slice(0, 10).map((t) => (
                <div
                  key={t.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "6px 0",
                    borderBottom: `1px solid ${C.border}`,
                  }}
                >
                  <span style={{ color: C.textDim, fontSize: 13 }}>
                    {t.description}
                  </span>
                  <span style={{ color: C.red, fontSize: 12, fontWeight: 600 }}>
                    low confidence
                  </span>
                </div>
              ))
            )}
          </div>
        );
      })()}
    </div>
  );
}
