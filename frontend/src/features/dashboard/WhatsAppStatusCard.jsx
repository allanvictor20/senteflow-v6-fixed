import { useState, useEffect } from "react";
import { C, s } from "../../utils/theme";
import { getWhatsAppStatus } from "../../services/api";

export default function WhatsAppStatusCard() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getWhatsAppStatus().then((s) => {
      setStatus(s);
      setLoading(false);
    });
    const interval = setInterval(() => {
      getWhatsAppStatus().then(setStatus);
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const isOnline = status?.session?.status === "CONNECTED" || status?.health?.status === "ok";
  const dotColor = loading ? C.textMuted : isOnline ? C.green : C.red;
  const label = loading ? "Checking..." : isOnline ? "Connected" : "Offline";

  return (
    <div style={{ ...s.card, display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ position: "relative", flexShrink: 0 }}>
        <div style={{ fontSize: 28 }}>📱</div>
        <div style={{
          position: "absolute", bottom: 0, right: 0,
          width: 10, height: 10, borderRadius: "50%",
          background: dotColor,
          border: `2px solid ${C.surface}`,
          boxShadow: isOnline ? `0 0 6px ${C.green}` : "none",
        }} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ color: C.textMuted, fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
          WhatsApp Bot
        </div>
        <div style={{ color: dotColor, fontSize: 14, fontWeight: 700 }}>{label}</div>
        {!loading && !isOnline && (
          <div style={{ color: C.textMuted, fontSize: 11, marginTop: 2 }}>
            Connect WhatsApp via Evolution API
          </div>
        )}
        {isOnline && status?.session?.name && (
          <div style={{ color: C.textMuted, fontSize: 11, marginTop: 2 }}>
            Session: {status.session.name}
          </div>
        )}
      </div>
    </div>
  );
}