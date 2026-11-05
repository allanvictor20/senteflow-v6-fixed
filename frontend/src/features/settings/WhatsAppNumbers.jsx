import { useEffect, useState } from "react";
import { C, s } from "../../utils/theme";
import { listWhatsAppNumbers, linkWhatsAppNumber, unlinkWhatsAppNumber } from "../../services/api";

/**
 * WhatsApp numbers that may use SenteFlow for this business.
 * Messages from any other number get no data. Linked numbers also receive
 * the morning briefing and payment reminders.
 */
export default function WhatsAppNumbers({ orgId }) {
  const [phones, setPhones] = useState([]);
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const res = await listWhatsAppNumbers(orgId);
      setPhones(res.phones || []);
      setError("");
    } catch (e) {
      setError(e.message || "Could not load numbers");
    }
  }

  useEffect(() => { load(); }, [orgId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function add() {
    if (!phone.trim()) return;
    setBusy(true);
    try {
      await linkWhatsAppNumber(orgId, phone.trim(), label.trim());
      setPhone("");
      setLabel("");
      await load();
    } catch (e) {
      setError(e.status === 409 ? "That number is already linked to another business." : (e.message || "Could not add number"));
    } finally {
      setBusy(false);
    }
  }

  async function remove(p) {
    setBusy(true);
    try {
      await unlinkWhatsAppNumber(orgId, p);
      await load();
    } catch (e) {
      setError(e.message || "Could not remove number");
    } finally {
      setBusy(false);
    }
  }

  const rowStyle = { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: `1px solid ${C.border}` };

  return (
    <div style={s.section}>
      <p style={s.sectionTitle}>WhatsApp numbers</p>
      <p style={{ color: C.textMuted, fontSize: 12, margin: "4px 0 12px", lineHeight: 1.5 }}>
        Only these numbers can talk to SenteFlow for this business. They also get the
        morning briefing (7am) and payment reminders.
      </p>

      {phones.length === 0 && !error && (
        <div style={{ color: C.textDim, fontSize: 13, padding: "8px 0" }}>No numbers linked yet.</div>
      )}
      {phones.map((p) => (
        <div key={p.phone} style={rowStyle}>
          <div>
            <div style={{ color: C.text, fontSize: 14, fontWeight: 600 }}>+{p.phone}</div>
            {p.label && <div style={{ color: C.textMuted, fontSize: 12 }}>{p.label}</div>}
          </div>
          <button style={s.logoutBtn} disabled={busy} onClick={() => remove(p.phone)} aria-label={`Remove +${p.phone}`}>
            Remove
          </button>
        </div>
      ))}

      <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        <input
          style={{ ...s.input, flex: "2 1 180px" }}
          placeholder="+256 700 000 000"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <input
          style={{ ...s.input, flex: "1 1 120px" }}
          placeholder="Label (e.g. Owner)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button style={s.primaryBtn} disabled={busy || !phone.trim()} onClick={add}>
          Add number
        </button>
      </div>
      {error && <div style={{ color: C.red, fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
