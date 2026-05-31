import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";

export default function CustomerDetailView({ customer, onBack }) {
  const outstanding = customer.totalOwed || 0;
  const paid = customer.totalPaid || 0;
  const events = customer.events || [];

  return (
    <div style={s.page}>
      <button style={{ ...s.ghostBtn, marginBottom: 16 }} onClick={onBack}>Back</button>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>{customer.name}</h2>
          <p style={s.pageSubtitle}>Last contact {customer.lastContact ? String(customer.lastContact).slice(0, 16) : "unknown"}</p>
        </div>
      </div>

      <div style={s.cardGrid4}>
        <div style={s.card}><div style={s.sectionTitle}>Total paid</div><div style={{ color: C.green, fontWeight: 800 }}>{fmt(paid)}</div></div>
        <div style={s.card}><div style={s.sectionTitle}>Outstanding</div><div style={{ color: outstanding ? C.yellow : C.text, fontWeight: 800 }}>{fmt(outstanding)}</div></div>
        <div style={s.card}><div style={s.sectionTitle}>Events</div><div style={{ color: C.text, fontWeight: 800 }}>{events.length}</div></div>
        <div style={s.card}><div style={s.sectionTitle}>Suggested action</div><div style={{ color: C.text, fontWeight: 700 }}>{outstanding ? "Follow up" : "Keep warm"}</div></div>
      </div>

      <div style={s.section}>
        <h3 style={{ ...s.sectionTitle, marginBottom: 12 }}>Conversation History</h3>
        {events.slice(0, 8).map((item, index) => (
          <div key={item.id || index} style={{ padding: "8px 0", borderBottom: index < Math.min(events.length, 8) - 1 ? `1px solid ${C.border}` : "none" }}>
            <div style={{ color: C.text, fontSize: 13, fontWeight: 700 }}>{item.event_type || item.transaction_type || item.type || "activity"}</div>
            <div style={{ color: C.textMuted, fontSize: 12 }}>{item.description || item.raw_message || "No description"}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
