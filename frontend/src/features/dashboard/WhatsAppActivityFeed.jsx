import { C, s } from "../../utils/theme";

export default function WhatsAppActivityFeed({ transactions }) {
  const waTransactions = transactions
    .filter((t) => t.source === "whatsapp" || !t.source)
    .slice(0, 8);

  const relativeTime = (ts) => {
    if (!ts) return "";
    const diff = (Date.now() - new Date(ts).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return new Date(ts).toLocaleDateString();
  };

  const getActivityIcon = (type) => {
    const icons = {
      contribution: "💚", income: "💚", payment: "💰",
      expense: "🔴", loan: "🏦", loan_repayment: "↩️",
      withdrawal: "💸", fine: "⚠️",
    };
    return icons[type] || "📝";
  };

  const getActivityText = (txn) => {
    const person = txn.payer || txn.payee || txn.member_name || "Someone";
    const amount = `${txn.currency || "UGX"} ${Number(txn.amount).toLocaleString()}`;
    const type = txn.transaction_type || txn.type || "transaction";
    if (type === "contribution" || type === "payment") {
      return { primary: `${person} paid ${amount}`, secondary: txn.description };
    }
    if (type === "loan") {
      return { primary: `Loan issued to ${person}: ${amount}`, secondary: txn.description };
    }
    if (type === "expense") {
      return { primary: `Expense recorded: ${amount}`, secondary: txn.description };
    }
    return { primary: txn.description || `${type} — ${amount}`, secondary: person };
  };

  return (
    <div style={{ ...s.card, padding: "16px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h3 style={{ ...s.sectionTitle, margin: 0 }}>📲 WhatsApp Activity</h3>
        <span style={{ color: C.textMuted, fontSize: 11 }}>Live feed</span>
      </div>

      {waTransactions.length === 0 ? (
        <div style={{ textAlign: "center", padding: "24px 0", color: C.textMuted }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>💬</div>
          <div style={{ fontSize: 13 }}>No WhatsApp activity yet.</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>
            Send a receipt photo or voice note to your bot to get started.
          </div>
        </div>
      ) : (
        waTransactions.map((txn, i) => {
          const { primary, secondary } = getActivityText(txn);
          return (
            <div
              key={txn.id || i}
              style={{
                display: "flex", gap: 12, padding: "10px 0",
                borderBottom: i < waTransactions.length - 1 ? `1px solid ${C.border}` : "none",
              }}
            >
              <div style={{ fontSize: 20, flexShrink: 0, marginTop: 1 }}>
                {getActivityIcon(txn.transaction_type || txn.type)}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: C.text, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {primary}
                </div>
                {secondary && (
                  <div style={{ color: C.textMuted, fontSize: 12, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {secondary}
                  </div>
                )}
              </div>
              <div style={{ color: C.textMuted, fontSize: 11, flexShrink: 0, marginTop: 2 }}>
                {relativeTime(txn.created_at || txn.timestamp)}
              </div>
            </div>
          );
        })
      )}

      <div style={{ marginTop: 14, padding: "10px 14px", background: C.surfaceHigh, borderRadius: 8 }}>
        <div style={{ color: C.textMuted, fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
          💡 How to send via WhatsApp:
        </div>
        <div style={{ color: C.textMuted, fontSize: 11, lineHeight: 1.6 }}>
          📸 Photo → receipt auto-extracted<br />
          🎤 Voice note → transcribed + recorded<br />
          ✍️ Text: "Brian paid 50k for feed"<br />
          📊 Text: "summary" → get balance
        </div>
      </div>
    </div>
  );
}