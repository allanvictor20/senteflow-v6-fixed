import { useState } from "react";
import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";

function eventTypeOf(item) {
  return item.event_type || item.transaction_type || item.type || "business_note";
}

function eventLabel(type) {
  return String(type).replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function eventAmount(item) {
  return item.amount || item.entities?.amount || 0;
}

export default function ActivityFeedView({ transactions = [] }) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const filters = [
    { key: "all", label: "All" },
    { key: "payment_received", label: "Payments" },
    { key: "customer_order", label: "Orders" },
    { key: "payment_promise", label: "Promises" },
    { key: "expense_recorded", label: "Expenses" },
    { key: "low_stock_alert", label: "Stock" },
  ];

  const events = transactions.filter((item) => {
    const type = eventTypeOf(item);
    if (filter !== "all" && type !== filter && item.transaction_type !== filter) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return [
      type,
      item.description,
      item.raw_message,
      item.payer,
      item.payee,
      item.member_name,
      item.customer_name,
      item.entities?.item,
      item.entities?.customer,
    ].filter(Boolean).join(" ").toLowerCase().includes(q);
  });

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Activity Feed</h2>
          <p style={s.pageSubtitle}>{events.length} business events from WhatsApp and uploads</p>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        {filters.map((item) => (
          <button
            key={item.key}
            style={{
              ...s.ghostBtn,
              background: filter === item.key ? C.accent : "none",
              color: filter === item.key ? C.white : C.textDim,
              borderColor: filter === item.key ? C.accent : C.border,
              padding: "6px 12px",
              fontSize: 13,
            }}
            onClick={() => setFilter(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <input
        style={{ ...s.input, width: "100%", marginBottom: 16 }}
        aria-label="Search activity"
        placeholder="Search activity..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      {events.length === 0 ? (
        <div style={{ textAlign: "center", padding: "48px 0", color: C.textMuted }}>
          No activity matches this view.
        </div>
      ) : (
        events.map((item) => {
          const type = eventTypeOf(item);
          const amount = eventAmount(item);
          const time = item.timestamp || item.created_at || item.date || "";
          return (
            <div key={item.id || item.event_id || item.transaction_hash} style={{ ...s.section, display: "grid", gridTemplateColumns: "36px 1fr auto", gap: 12, alignItems: "center" }}>
              <div style={{ width: 32, height: 32, borderRadius: 8, background: C.accentGlow, display: "flex", alignItems: "center", justifyContent: "center", color: C.accentLight }}>
                {type.includes("payment") ? "$" : type.includes("order") ? "#" : type.includes("stock") ? "!" : "."}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ color: C.text, fontWeight: 700, fontSize: 14 }}>{eventLabel(type)}</div>
                <div style={{ color: C.textMuted, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.description || item.raw_message || item.entities?.item || "Business event"}
                </div>
                {time && <div style={{ color: C.textMuted, fontSize: 11, marginTop: 3 }}>{String(time).slice(0, 16)}</div>}
              </div>
              <div style={{ color: amount ? C.green : C.textMuted, fontWeight: 800, fontSize: 13 }}>
                {amount ? fmt(amount) : ""}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
