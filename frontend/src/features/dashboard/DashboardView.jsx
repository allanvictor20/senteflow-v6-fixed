import { useState, useEffect } from "react";
import { C, s } from "../../utils/theme";
import { fmt } from "../../utils/format";
import { getCustomers, getMediaAssets, getOrders } from "../../services/api";
import KpiCard from "./KpiCard";
import WhatsAppStatusCard from "./WhatsAppStatusCard";
import WhatsAppActivityFeed from "./WhatsAppActivityFeed";

export default function DashboardView({ summary, summaryError, transactions, alerts, orgId }) {
  const [controlData, setControlData] = useState({ orders: [], customers: [], media_assets: [] });
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    let active = true;
    setFetchError(false);
    // allSettled, not all: one failing panel shouldn't blank the other two.
    Promise.allSettled([
      getOrders(orgId),
      getCustomers(orgId),
      getMediaAssets(orgId),
    ]).then(([orders, customers, media]) => {
      if (!active) return;
      setControlData({
        orders: orders.value?.orders || [],
        customers: customers.value?.customers || [],
        media_assets: media.value?.media_assets || [],
      });
      setFetchError([orders, customers, media].some((r) => r.status === "rejected"));
    });
    return () => { active = false; };
  }, [orgId, transactions.length]);

  const revenueThisWeek = transactions.reduce((total, t) => {
    if (!t.created_at) return total;
    const diff = (Date.now() - new Date(t.created_at).getTime()) / (1000 * 60 * 60 * 24);
    const type = t.event_type || t.type || t.transaction_type;
    return diff <= 7 && ["payment_received", "payment", "income"].includes(type)
      ? total + Number(t.amount || t.entities?.amount || 0)
      : total;
  }, 0);
  const pendingOrders = controlData.orders.filter((o) => o.status !== "completed");
  const unpaidOrders = controlData.orders.filter((o) => o.payment_status === "unpaid");
  const pendingDeliveries = controlData.orders.filter((o) => o.delivery_status === "pending");
  const recentMedia = controlData.media_assets.slice(0, 5);

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Business Pulse</h2>
          <p style={s.pageSubtitle}>Recent WhatsApp conversations, business events, and follow-up signals</p>
        </div>
      </div>

      {/* Summary Error Banner */}
      {summaryError && (
        <div style={{ ...s.section, background: C.redDim, borderColor: C.red + "44", marginBottom: 16 }}>
          <span style={{ color: C.red, fontSize: 13 }}>
            ⚠ Could not load financial summary. KPI totals may be stale.
          </span>
        </div>
      )}

      {/* KPI Row */}
      <div style={s.cardGrid4}>
        <KpiCard label="Events Today" value={transactions.length} color={C.accent} sub="from WhatsApp and uploads" />
        <KpiCard label="Follow-ups Needed" value={pendingDeliveries.length + alerts.length} color={C.yellow} />
        <KpiCard label="Revenue This Week" value={fmt(revenueThisWeek || summary.total_income || 0)} color={C.green} />
        <KpiCard label="Customers Active" value={controlData.customers.length} color={C.white} sub={`${unpaidOrders.length} unresolved`} />
      </div>

      <div style={s.cardGrid4}>
        <KpiCard label="Known Customers" value={controlData.customers.length} color={C.white} sub="remembered from WhatsApp" />
        <KpiCard label="Inventory Alerts" value={alerts.length} color={C.red} />
        <KpiCard label="Extraction History" value={recentMedia.length} color={C.green} sub="receipts and voice notes" />
        <KpiCard label="Open Orders" value={pendingOrders.length} color={C.accent} sub="remembered activity" />
      </div>

      {/* WhatsApp Status */}
      <div style={{ marginBottom: 20 }}>
        <WhatsAppStatusCard />
      </div>

      {/* Fetch Error Banner */}
      {fetchError && (
        <div style={{ ...s.section, background: C.redDim, borderColor: C.red + "44", marginBottom: 16 }}>
          <span style={{ color: C.red, fontSize: 13 }}>
            ⚠ Could not load dashboard data. Check your connection or backend status.
          </span>
        </div>
      )}

      {/* Alerts */}
      {alerts.length > 0 && (
        <div style={{ ...s.section, background: C.yellowDim, borderColor: C.yellow + "44", marginBottom: 20 }}>
          <div style={{ color: C.yellow, fontSize: 12, fontWeight: 700, marginBottom: 10 }}>
            ⚠ {alerts.length} ALERT{alerts.length > 1 ? "S" : ""} DETECTED
          </div>
          {alerts.map((a, i) => (
            <div key={i} style={{ color: C.textDim, fontSize: 13, padding: "4px 0", borderBottom: i < alerts.length - 1 ? `1px solid ${C.border}` : "none" }}>
              {a.msg}
            </div>
          ))}
        </div>
      )}

      {/* Two-column layout: Activity Feed + Recent Transactions */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 20, marginBottom: 20 }}>
        {/* WhatsApp Activity Feed */}
        <WhatsAppActivityFeed transactions={transactions} />

        {/* Recent Transactions */}
        <div style={{ ...s.card, padding: "16px 20px" }}>
          <h3 style={{ ...s.sectionTitle, marginTop: 0, marginBottom: 16 }}>Recent Events</h3>

          {transactions.length === 0 ? (
            <div style={{ textAlign: "center", padding: "24px 0" }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
              <p style={{ color: C.textMuted, margin: "0 0 16px", fontSize: 13 }}>
                No transactions yet. Send a receipt or voice note to your WhatsApp bot.
              </p>
            </div>
          ) : (
            transactions.slice(0, 8).map((t) => (
              <div key={t.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "8px 0", borderBottom: `1px solid ${C.border}` }}>
                <div style={{ flex: 1, minWidth: 0, marginRight: 8 }}>
                  <div style={{ color: C.text, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {t.description}
                  </div>
                  <div style={{ color: C.textMuted, fontSize: 11, marginTop: 2 }}>
                    {t.payer || t.payee || t.category} · {t.date}
                    {t.source === "whatsapp" && (
                      <span style={{ marginLeft: 6, color: C.green, fontWeight: 600 }}>📱 WA</span>
                    )}
                  </div>
                </div>
                <span style={{ color: ["contribution", "income", "payment"].includes(t.type || t.transaction_type) ? C.green : C.red, fontWeight: 700, fontSize: 13, flexShrink: 0 }}>
                  {["contribution", "income", "payment"].includes(t.type || t.transaction_type) ? "+" : "-"}
                  {t.currency || "UGX"} {Number(t.amount).toLocaleString()}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 20 }}>
        <div style={{ ...s.card, padding: "16px 20px" }}>
          <h3 style={{ ...s.sectionTitle, marginTop: 0, marginBottom: 16 }}>Conversation Memory</h3>
          {pendingOrders.length === 0 ? (
            <div style={{ color: C.textMuted, fontSize: 13 }}>No unresolved conversation threads.</div>
          ) : (
            pendingOrders.slice(0, 6).map((order) => (
              <div key={order.id} style={{ padding: "8px 0", borderBottom: `1px solid ${C.border}` }}>
                <div style={{ color: C.text, fontSize: 13, fontWeight: 700 }}>
                  {order.customer_name || order.customer_id || "Customer"}
                </div>
                <div style={{ color: C.textMuted, fontSize: 12, marginTop: 2 }}>
                  {(order.items || order.source_message || "Order").toString()} · {order.payment_status || "unpaid"} · {order.delivery_status || "pending"}
                </div>
              </div>
            ))
          )}
        </div>

        <div style={{ ...s.card, padding: "16px 20px" }}>
          <h3 style={{ ...s.sectionTitle, marginTop: 0, marginBottom: 16 }}>Extraction History</h3>
          {recentMedia.length === 0 ? (
            <div style={{ color: C.textMuted, fontSize: 13 }}>No receipt or voice media processed yet.</div>
          ) : (
            recentMedia.map((asset) => (
              <div key={asset.id} style={{ padding: "8px 0", borderBottom: `1px solid ${C.border}` }}>
                <div style={{ color: C.text, fontSize: 13, fontWeight: 700 }}>
                  {asset.filename || asset.message_type || "WhatsApp media"}
                </div>
                <div style={{ color: C.textMuted, fontSize: 12, marginTop: 2 }}>
                  {asset.extraction_status || "saved"} · {asset.mime_type || "unknown type"}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
