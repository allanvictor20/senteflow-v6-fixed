import { useMemo, useState } from "react";
import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";
import CustomerDetailView from "./CustomerDetailView";

export default function CustomersView({ transactions = [] }) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);

  const customers = useMemo(() => {
    const map = {};
    transactions.forEach((item) => {
      const name = item.customer_name || item.member_name || item.payer || item.payee || item.entities?.customer || item.entities?.payer;
      if (!name) return;
      if (!map[name]) {
        map[name] = { name, events: [], totalPaid: 0, totalOwed: 0, lastContact: "" };
      }
      map[name].events.push(item);
      map[name].lastContact = item.timestamp || item.created_at || item.date || map[name].lastContact;
      const type = item.event_type || item.transaction_type || item.type;
      const amount = Number(item.amount || item.entities?.amount || 0);
      if (["payment_received", "payment", "income"].includes(type)) {
        map[name].totalPaid += amount;
      } else if (["debt_created", "payment_promise"].includes(type)) {
        map[name].totalOwed += amount;
      }
    });
    return Object.values(map).sort((a, b) => b.events.length - a.events.length);
  }, [transactions]);

  const filtered = customers.filter((customer) => customer.name.toLowerCase().includes(search.toLowerCase()));

  if (selected) {
    return <CustomerDetailView customer={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Customers</h2>
          <p style={s.pageSubtitle}>{customers.length} customer profiles from business activity</p>
        </div>
      </div>

      <input
        style={{ ...s.input, width: "100%", marginBottom: 16 }}
        aria-label="Search customers"
        placeholder="Search customers..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
        {filtered.map((customer) => (
          <button
            key={customer.name}
            style={{ ...s.card, textAlign: "left", cursor: "pointer" }}
            onClick={() => setSelected(customer)}
          >
            <div style={{ width: 38, height: 38, borderRadius: 8, background: C.accentGlow, color: C.accentLight, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, marginBottom: 10 }}>
              {customer.name[0].toUpperCase()}
            </div>
            <div style={{ color: C.text, fontWeight: 800, fontSize: 14 }}>{customer.name}</div>
            <div style={{ color: C.textMuted, fontSize: 12, marginTop: 3 }}>{customer.events.length} event{customer.events.length === 1 ? "" : "s"}</div>
            <div style={{ display: "flex", gap: 10, marginTop: 12, color: C.textDim, fontSize: 12 }}>
              <span>Paid {fmt(customer.totalPaid)}</span>
              <span>Open {fmt(customer.totalOwed)}</span>
            </div>
          </button>
        ))}
      </div>

      {filtered.length === 0 && <div style={{ color: C.textMuted, padding: "40px 0", textAlign: "center" }}>No customers found yet.</div>}
    </div>
  );
}
