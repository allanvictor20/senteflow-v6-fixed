import { useEffect, useState } from "react";
import { signInWithGoogle, logOut } from "./firebase/config";
import { useAuth, useTransactions, useSummary, useAlerts, useIsMobile } from "./hooks/index";
import { AppProvider, useAppState, useAppDispatch, setView, notify, clearNotification } from "./stores/appStore";
import { C, s, globalCSS } from "./utils/theme";

import DashboardView from "./features/dashboard/DashboardView";
import ActivityFeedView from "./features/activity/ActivityFeedView";
import CustomersView from "./features/customers/CustomersView";
import AlertsView from "./features/alerts/AlertsView";
import AskSenteFlow from "./features/ask/AskSenteFlow";
import BusinessMemoryView from "./features/memory/BusinessMemoryView";
import ReviewView from "./features/review/ReviewView";

// Must match the backend's DEFAULT_ORG_ID. They were "demo-org" and "default"
// respectively, so the dashboard read a different org than the bot wrote to.
const ORG_ID = process.env.REACT_APP_ORG_ID || "default";

function LoginPage({ onLogin, error }) {
  return (
    <div style={s.loginPage}>
      <div style={s.loginCard}>
        <div style={s.loginGlow} />
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ ...s.logoIcon, margin: "0 auto 16px", width: 52, height: 52, fontSize: 22 }}>SF</div>
          <h1 style={{ color: C.white, fontSize: 22, fontWeight: 800, margin: "0 0 6px" }}>SenteFlow AI</h1>
          <p style={{ color: C.textMuted, fontSize: 13, margin: 0 }}>WhatsApp-native business memory assistant</p>
        </div>
        {error && (
          <div style={{ background: C.redDim, border: `1px solid ${C.red}44`, borderRadius: 8, padding: "10px 14px", marginBottom: 16, color: C.red, fontSize: 13 }}>
            {error}
          </div>
        )}
        <button style={s.googleBtn} onClick={onLogin}>Continue with Google</button>
        <p style={{ color: C.textMuted, fontSize: 11, textAlign: "center", marginTop: 16, lineHeight: 1.5 }}>
          For small business owners who run their day through WhatsApp
        </p>
      </div>
    </div>
  );
}

function Toast({ notification, onClose }) {
  if (!notification) return null;
  return (
    <div style={{ ...s.toast, background: notification.type === "error" ? C.red : notification.type === "warning" ? C.yellow : C.green }}>
      {notification.msg}
      <button style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", marginLeft: 10, fontSize: 14 }} onClick={onClose}>
        x
      </button>
    </div>
  );
}

function Sidebar({ activeView, onNav, user, alertCount, onLogout }) {
  // Navigation order per refactor guide:
  // 1. Home (Business Pulse)
  // 2. Activity Feed
  // 3. Customers
  // 4. Tasks / Follow-ups
  // 5. Ask SenteFlow
  // 6. Business Memory
  // 7. Alerts
  // 8. Settings
  const navItems = [
    { key: "dashboard",  icon: "P", label: "Business Pulse" },
    { key: "activity",   icon: "A", label: "Activity Feed" },
    { key: "customers",  icon: "C", label: "Customers" },
    { key: "tasks",      icon: "T", label: "Tasks" },
    { key: "ask",        icon: "?", label: "Ask SenteFlow" },
    { key: "memory",     icon: "M", label: "Business Memory" },
    { key: "alerts",     icon: "!", label: "Alerts", badge: alertCount },
    { key: "settings",   icon: "⚙", label: "Settings" },
  ];

  return (
    <div style={s.sidebar}>
      <div style={s.logo}>
        <div style={s.logoIcon}>SF</div>
        <div>
          <div style={{ color: C.white, fontWeight: 800, fontSize: 14 }}>SenteFlow</div>
          <div style={{ color: C.textMuted, fontSize: 11 }}>Business Memory</div>
        </div>
      </div>
      <nav style={{ flex: 1, padding: "8px 0" }}>
        {navItems.map((item) => (
          <button
            key={item.key}
            style={{ ...s.navBtn, ...(activeView === item.key ? s.navBtnActive : {}) }}
            onClick={() => onNav(item.key)}
          >
            <span style={{ fontSize: 12, width: 16, textAlign: "center" }} aria-hidden="true">{item.icon}</span>
            <span style={{ flex: 1 }}>{item.label}</span>
            {item.badge > 0 && <span style={s.badge}>{item.badge}</span>}
          </button>
        ))}
      </nav>
      {user && (
        <div style={s.sidebarFooter}>
          {user.photoURL ? (
            <img src={user.photoURL} alt="avatar" style={s.avatar} />
          ) : (
            <div style={{ ...s.avatar, background: C.accent, display: "flex", alignItems: "center", justifyContent: "center", color: C.white, fontWeight: 700 }}>
              {user.displayName?.[0] || "U"}
            </div>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: C.text, fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {user.displayName || user.email}
            </div>
            <div style={{ color: C.textMuted, fontSize: 11 }}>Business owner</div>
          </div>
          <button style={s.logoutBtn} aria-label="Sign out" onClick={onLogout}>x</button>
        </div>
      )}
    </div>
  );
}

// Priority label and color for each task type
const TASK_META = {
  payment_promise:   { label: "Payment Promise",   priority: "high",   emoji: "💰" },
  debt_created:      { label: "Debt Recorded",      priority: "high",   emoji: "📒" },
  customer_order:    { label: "Order",              priority: "medium", emoji: "🛍️" },
  order_received:    { label: "Order Received",     priority: "medium", emoji: "🛍️" },
  complaint:         { label: "Complaint",          priority: "high",   emoji: "⚠️" },
  follow_up_required:{ label: "Follow-up Required", priority: "medium", emoji: "🔔" },
  low_stock_alert:   { label: "Low Stock",          priority: "medium", emoji: "📦" },
  reminder_request:  { label: "Reminder",           priority: "low",    emoji: "⏰" },
  appointment_request:{ label: "Appointment",       priority: "low",    emoji: "📅" },
};

const PRIORITY_COLOR = {
  high:   "#f43f5e",
  medium: "#f59e0b",
  low:    "#10b981",
};

const TASK_TYPES = new Set(Object.keys(TASK_META));

function TasksView({ transactions = [], alerts = [] }) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  // Only pull genuine task-type transactions — no mixing with unrelated events
  const taskTransactions = transactions.filter((t) => {
    const type = t.event_type || t.transaction_type || t.type || "";
    return TASK_TYPES.has(type);
  });

  // Convert anomaly alerts into a consistent shape so they render the same way
  const alertTasks = alerts.map((a) => ({
    id: a.id,
    _source: "alert",
    event_type: "low_stock_alert",
    description: a.msg,
    created_at: null,
    payer: null,
    amount: null,
  }));

  const allTasks = [...taskTransactions, ...alertTasks];

  // Filter by priority tab
  const priorityFiltered =
    filter === "all"
      ? allTasks
      : allTasks.filter((t) => {
          const type = t.event_type || t.transaction_type || t.type || "";
          return (TASK_META[type]?.priority || "low") === filter;
        });

  // Filter by search string (customer name, description, raw message)
  const searchLower = search.trim().toLowerCase();
  const visible = searchLower
    ? priorityFiltered.filter((t) =>
        [t.payer, t.description, t.raw_message, t.message, t.customer]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(searchLower))
      )
    : priorityFiltered;

  const counts = {
    all:    allTasks.length,
    high:   allTasks.filter((t) => (TASK_META[t.event_type || t.transaction_type || t.type]?.priority) === "high").length,
    medium: allTasks.filter((t) => (TASK_META[t.event_type || t.transaction_type || t.type]?.priority) === "medium").length,
    low:    allTasks.filter((t) => (TASK_META[t.event_type || t.transaction_type || t.type]?.priority) === "low").length,
  };

  const tabStyle = (key) => ({
    background: filter === key ? C.accentGlow : "none",
    color: filter === key ? C.accentLight : C.textMuted,
    border: `1px solid ${filter === key ? C.accent : C.border}`,
    borderRadius: 8,
    padding: "6px 14px",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: filter === key ? 700 : 400,
  });

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Tasks / Follow-ups</h2>
          <p style={s.pageSubtitle}>
            {allTasks.length} item{allTasks.length !== 1 ? "s" : ""} need attention
          </p>
        </div>
      </div>

      {/* Search */}
      <div style={{ marginBottom: 14 }}>
        <input
          style={{ ...s.input, width: "100%", maxWidth: 360 }}
          placeholder="Search by customer, description…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Priority filter tabs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
        {["all", "high", "medium", "low"].map((key) => (
          <button key={key} style={tabStyle(key)} onClick={() => setFilter(key)}>
            {key === "all" ? "All" : key.charAt(0).toUpperCase() + key.slice(1)}
            {counts[key] > 0 && (
              <span style={{
                marginLeft: 6, background: key === "high" ? C.red : key === "medium" ? C.yellow : C.green,
                color: C.white, borderRadius: 10, fontSize: 11, padding: "1px 6px", fontWeight: 700,
              }}>
                {counts[key]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Task cards */}
      {visible.slice(0, 50).map((item, index) => {
        const type = item.event_type || item.transaction_type || item.type || "";
        const meta = TASK_META[type] || { label: type.replaceAll("_", " "), priority: "low", emoji: "📋" };
        const priorityColor = PRIORITY_COLOR[meta.priority];
        const dateStr = item.created_at
          ? new Date(item.created_at).toLocaleDateString("en-UG", { day: "numeric", month: "short" })
          : null;

        return (
          <div
            key={item.id || index}
            style={{
              ...s.section,
              borderLeft: `3px solid ${priorityColor}`,
              display: "flex",
              gap: 14,
              alignItems: "flex-start",
            }}
          >
            <span style={{ fontSize: 20, flexShrink: 0, marginTop: 2 }}>{meta.emoji}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                <span style={{ color: C.white, fontWeight: 700, fontSize: 14 }}>{meta.label}</span>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 6, fontWeight: 600,
                    background: `${priorityColor}22`, color: priorityColor,
                  }}>
                    {meta.priority}
                  </span>
                  {dateStr && <span style={{ color: C.textMuted, fontSize: 12 }}>{dateStr}</span>}
                </div>
              </div>
              {item.payer && (
                <div style={{ color: C.textDim, fontSize: 13, marginTop: 4 }}>
                  Customer: <strong style={{ color: C.text }}>{item.payer}</strong>
                </div>
              )}
              {item.amount && (
                <div style={{ color: C.textDim, fontSize: 13 }}>
                  Amount: <strong style={{ color: C.green }}>UGX {Number(item.amount).toLocaleString()}</strong>
                </div>
              )}
              <div style={{ color: C.textMuted, fontSize: 13, marginTop: 4, wordBreak: "break-word" }}>
                {item.description || item.raw_message || item.message || "Needs attention"}
              </div>
            </div>
          </div>
        );
      })}

      {visible.length === 0 && (
        <div style={{ color: C.textMuted, textAlign: "center", padding: 48 }}>
          {search ? "No tasks match your search." : "No follow-ups waiting. You're on top of things! ✅"}
        </div>
      )}
    </div>
  );
}

function SettingsView() {
  const [saved, setSaved] = useState(false);
  const [ownerPhone, setOwnerPhone] = useState(
    () => localStorage.getItem("sf_owner_phone") || ""
  );
  const [businessName, setBusinessName] = useState(
    () => localStorage.getItem("sf_business_name") || ""
  );
  const [currency, setCurrency] = useState(
    () => localStorage.getItem("sf_currency") || "UGX"
  );
  const [briefingEnabled, setBriefingEnabled] = useState(
    () => localStorage.getItem("sf_briefing") !== "false"
  );

  function handleSave() {
    localStorage.setItem("sf_owner_phone", ownerPhone);
    localStorage.setItem("sf_business_name", businessName);
    localStorage.setItem("sf_currency", currency);
    localStorage.setItem("sf_briefing", String(briefingEnabled));
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  }

  const fieldStyle = { ...s.input, width: "100%", marginTop: 8 };
  const labelStyle = { color: C.textDim, fontSize: 13, fontWeight: 600, display: "block", marginTop: 16 };
  const rowStyle = { display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 12, borderBottom: `1px solid ${C.border}` };

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Settings</h2>
          <p style={s.pageSubtitle}>Configure SenteFlow for your business</p>
        </div>
      </div>

      {/* Business profile */}
      <div style={s.section}>
        <p style={s.sectionTitle}>Business Profile</p>
        <label style={labelStyle}>
          Business name
          <input
            style={fieldStyle}
            placeholder="e.g. Kamu General Supplies"
            value={businessName}
            onChange={(e) => setBusinessName(e.target.value)}
          />
        </label>
        <label style={labelStyle}>
          Default currency
          <select
            style={{ ...s.select, width: "100%", marginTop: 8 }}
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          >
            <option value="UGX">UGX — Ugandan Shilling</option>
            <option value="KES">KES — Kenyan Shilling</option>
            <option value="TZS">TZS — Tanzanian Shilling</option>
            <option value="RWF">RWF — Rwandan Franc</option>
            <option value="USD">USD — US Dollar</option>
          </select>
        </label>
      </div>

      {/* Notifications */}
      <div style={s.section}>
        <p style={s.sectionTitle}>Notifications</p>
        <div style={rowStyle}>
          <div>
            <div style={{ color: C.text, fontSize: 14, fontWeight: 600 }}>Daily morning briefing</div>
            <div style={{ color: C.textMuted, fontSize: 12, marginTop: 2 }}>
              WhatsApp summary of overdue payments, low stock, and revenue trend
            </div>
          </div>
          <button
            onClick={() => setBriefingEnabled((v) => !v)}
            style={{
              width: 44, height: 24, borderRadius: 12, border: "none", cursor: "pointer",
              background: briefingEnabled ? C.green : C.border,
              position: "relative", flexShrink: 0, transition: "background 0.2s",
            }}
            aria-label={briefingEnabled ? "Disable briefing" : "Enable briefing"}
          >
            <span style={{
              position: "absolute", top: 3, left: briefingEnabled ? 22 : 3,
              width: 18, height: 18, borderRadius: "50%", background: C.white,
              transition: "left 0.2s",
            }} />
          </button>
        </div>
        <label style={{ ...labelStyle, marginTop: 16 }}>
          Owner WhatsApp number (for briefings &amp; alerts)
          <input
            style={fieldStyle}
            placeholder="+256 700 000 000"
            value={ownerPhone}
            onChange={(e) => setOwnerPhone(e.target.value)}
          />
        </label>
      </div>

      {/* About */}
      <div style={s.section}>
        <p style={s.sectionTitle}>About</p>
        <div style={{ ...rowStyle, borderBottom: "none", paddingBottom: 0 }}>
          <span style={{ color: C.textMuted, fontSize: 13 }}>Version</span>
          <span style={{ color: C.textDim, fontSize: 13 }}>SenteFlow v6</span>
        </div>
        <div style={{ ...rowStyle, borderBottom: "none", paddingBottom: 0, marginTop: 12 }}>
          <span style={{ color: C.textMuted, fontSize: 13 }}>Backend</span>
          <span style={{ color: C.textDim, fontSize: 13 }}>FastAPI + Firestore</span>
        </div>
        <div style={{ ...rowStyle, borderBottom: "none", paddingBottom: 0, marginTop: 12 }}>
          <span style={{ color: C.textMuted, fontSize: 13 }}>AI providers</span>
          <span style={{ color: C.textDim, fontSize: 13 }}>Gemini → Claude → GPT-4o-mini</span>
        </div>
      </div>

      {/* Save button */}
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 4 }}>
        <button style={s.primaryBtn} onClick={handleSave}>
          Save settings
        </button>
        {saved && (
          <span style={{ color: C.green, fontSize: 13, fontWeight: 600 }}>
            ✓ Saved
          </span>
        )}
      </div>
    </div>
  );
}

function AppInner() {
  const user = useAuth();
  const { activeView, notification, extractionResult } = useAppState();
  const dispatch = useAppDispatch();
  const isMobile = useIsMobile();
  const { transactions, feedError } = useTransactions(ORG_ID);
  const { summary, summaryError } = useSummary(ORG_ID, transactions);
  const alerts = useAlerts(transactions);
  const [loginError, setLoginError] = useState(null);

  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = globalCSS;
    document.head.appendChild(style);
    return () => document.head.removeChild(style);
  }, []);

  useEffect(() => {
    if (feedError) dispatch(notify("Live feed disconnected. Refresh to reconnect.", "error"));
  }, [feedError, dispatch]);

  if (!user) {
    return (
      <LoginPage
        error={loginError}
        onLogin={async () => {
          setLoginError(null);
          try {
            await signInWithGoogle();
          } catch (err) {
            // Surface the error — common causes: popup blocked, wrong auth domain
            const msg =
              err?.code === "auth/popup-blocked"
                ? "Popup was blocked. Please allow popups for this site and try again."
                : err?.code === "auth/cancelled-popup-request"
                ? "Sign-in was cancelled. Please try again."
                : err?.message || "Sign-in failed. Please try again.";
            setLoginError(msg);
          }
        }}
      />
    );
  }

  const mobileNavItems = [
    { key: "dashboard",  icon: "P", label: "Home" },
    { key: "activity",   icon: "A", label: "Activity" },
    { key: "customers",  icon: "C", label: "Customers" },
    { key: "alerts",     icon: "!", label: "Alerts" },
  ];

  function renderView() {
    switch (activeView) {
      case "dashboard":
        return <DashboardView summary={summary} summaryError={summaryError} transactions={transactions} alerts={alerts} orgId={ORG_ID} />;

      // Activity Feed — absorbs old "transactions", "upload", "review" routes
      case "activity":
      case "transactions":
      case "upload":
        return <ActivityFeedView transactions={transactions} orgId={ORG_ID} />;

      // Review Events (formerly "Review Transactions")
      case "review":
        return <ReviewView result={extractionResult} onApprove={() => {}} onDiscard={() => {}} />;

      // Customers — absorbs old "members" route
      case "customers":
      case "members":
        return <CustomersView transactions={transactions} />;

      case "tasks":
        return <TasksView transactions={transactions} alerts={alerts} />;

      case "ask":
        return <AskSenteFlow transactions={transactions} alerts={alerts} />;

      case "memory":
        return <BusinessMemoryView transactions={transactions} />;

      case "alerts":
        return <AlertsView alerts={alerts} transactions={transactions} />;

      case "settings":
        return <SettingsView />;

      default:
        return <DashboardView summary={summary} summaryError={summaryError} transactions={transactions} alerts={alerts} orgId={ORG_ID} />;
    }
  }

  if (isMobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: C.bg, color: C.text }}>
        <Toast notification={notification} onClose={() => dispatch(clearNotification())} />
        <div style={s.mobileHeader}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ ...s.logoIcon, width: 28, height: 28, fontSize: 11 }}>SF</div>
            <span style={{ color: C.white, fontWeight: 700, fontSize: 14 }}>SenteFlow</span>
          </div>
          <button style={s.logoutBtn} aria-label="Sign out" onClick={() => logOut()}>x</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", paddingBottom: 60 }}>{renderView()}</div>
        <nav style={s.mobileNav}>
          {mobileNavItems.map((item) => (
            <button
              key={item.key}
              style={{ ...s.mobileNavBtn, ...(activeView === item.key ? s.mobileNavBtnActive : {}) }}
              onClick={() => dispatch(setView(item.key))}
            >
              <span style={{ fontSize: 14 }} aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
              {item.key === "alerts" && alerts.length > 0 && <span style={{ ...s.badge, position: "absolute", top: 4, right: "calc(50% - 18px)" }}>{alerts.length}</span>}
            </button>
          ))}
        </nav>
      </div>
    );
  }

  return (
    <div style={s.app}>
      <Toast notification={notification} onClose={() => dispatch(clearNotification())} />
      <Sidebar activeView={activeView} onNav={(view) => dispatch(setView(view))} user={user} alertCount={alerts.length} onLogout={() => logOut()} />
      <main style={s.main}>{renderView()}</main>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <AppInner />
    </AppProvider>
  );
}
