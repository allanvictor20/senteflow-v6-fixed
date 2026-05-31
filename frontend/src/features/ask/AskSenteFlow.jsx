import { useMemo, useState } from "react";
import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";

export default function AskSenteFlow({ transactions = [], alerts = [] }) {
  const [query, setQuery] = useState("What should I follow up today?");

  const answer = useMemo(() => {
    const q = query.toLowerCase();
    const debts = transactions.filter((item) => ["debt_created", "payment_promise"].includes(item.event_type || item.transaction_type || item.type));
    const payments = transactions.filter((item) => ["payment_received", "payment", "income"].includes(item.event_type || item.transaction_type || item.type));
    if (q.includes("owe") || q.includes("debt")) {
      const total = debts.reduce((sum, item) => sum + Number(item.amount || item.entities?.amount || 0), 0);
      return `${debts.length} open debt or promise records totaling ${fmt(total)}.`;
    }
    if (q.includes("follow")) {
      return `${debts.length + alerts.length} follow-ups need attention today. Start with payment promises and stock alerts.`;
    }
    if (q.includes("revenue") || q.includes("paid")) {
      const total = payments.reduce((sum, item) => sum + Number(item.amount || item.entities?.amount || 0), 0);
      return `Recorded payments total ${fmt(total)} across ${payments.length} events.`;
    }
    return "I can answer from the current activity feed. Try asking about debts, revenue, follow-ups, or customers.";
  }, [query, transactions, alerts]);

  const suggestions = ["Who owes me money?", "What should I follow up today?", "How much revenue is recorded?", "Which customers bought recently?"];

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Ask SenteFlow</h2>
          <p style={s.pageSubtitle}>Query your business activity without digging through records</p>
        </div>
      </div>

      <div style={s.section}>
        <input
          style={{ ...s.input, width: "100%", marginBottom: 12 }}
          aria-label="Ask SenteFlow"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          {suggestions.map((suggestion) => (
            <button key={suggestion} style={{ ...s.ghostBtn, padding: "6px 10px", fontSize: 12 }} onClick={() => setQuery(suggestion)}>
              {suggestion}
            </button>
          ))}
        </div>
        <div style={{ background: C.surfaceHigh, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16, color: C.text, lineHeight: 1.5 }}>
          {answer}
        </div>
      </div>
    </div>
  );
}
