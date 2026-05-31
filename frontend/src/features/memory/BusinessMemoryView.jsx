import { useMemo, useState } from "react";
import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";

export default function BusinessMemoryView({ transactions = [] }) {
  const [query, setQuery] = useState("");

  const memories = useMemo(() => {
    const byCustomer = {};
    transactions.forEach((item) => {
      const name = item.customer_name || item.member_name || item.payer || item.payee || item.entities?.customer || "Unknown";
      if (!byCustomer[name]) byCustomer[name] = { name, orders: 0, paid: 0, outstanding: 0, promises: 0, items: new Set() };
      const type = item.event_type || item.transaction_type || item.type;
      const amount = Number(item.amount || item.entities?.amount || 0);
      if (["customer_order", "order_received"].includes(type)) byCustomer[name].orders += 1;
      if (["payment_received", "payment", "income"].includes(type)) byCustomer[name].paid += amount;
      if (["debt_created", "payment_promise"].includes(type)) byCustomer[name].outstanding += amount;
      if (type === "payment_promise") byCustomer[name].promises += 1;
      const itemName = item.entities?.item || item.category;
      if (itemName) byCustomer[name].items.add(itemName);
    });
    return Object.values(byCustomer).map((memory) => ({ ...memory, items: Array.from(memory.items).slice(0, 3) }));
  }, [transactions]);

  const filtered = memories.filter((memory) => memory.name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Business Memory</h2>
          <p style={s.pageSubtitle}>Customer patterns, promises, and buying behavior</p>
        </div>
      </div>

      <input
        style={{ ...s.input, width: "100%", marginBottom: 16 }}
        aria-label="Search memory"
        placeholder="What do we know about Sarah?"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      <div style={{ display: "grid", gap: 12 }}>
        {filtered.map((memory) => (
          <div key={memory.name} style={s.section}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div>
                <div style={{ color: C.text, fontWeight: 800 }}>{memory.name}</div>
                <div style={{ color: C.textMuted, fontSize: 12, marginTop: 4 }}>
                  {memory.orders} orders - paid {fmt(memory.paid)} - open {fmt(memory.outstanding)}
                </div>
              </div>
              <div style={{ color: memory.promises ? C.yellow : C.green, fontWeight: 800, fontSize: 13 }}>
                {memory.promises ? `${memory.promises} open promise${memory.promises === 1 ? "" : "s"}` : "No open promises"}
              </div>
            </div>
            {memory.items.length > 0 && (
              <div style={{ color: C.textDim, fontSize: 12, marginTop: 10 }}>
                Usually buys: {memory.items.join(", ")}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
