import { useEffect, useState } from "react";
import { C, s } from "../../utils/theme";
import { getBusinessProfile, saveBusinessProfile } from "../../services/api";

const EMPTY = {
  name: "",
  business_type: "",
  location: "",
  currency: "UGX",
  language: "lug",
  reply_language: "english",
  credit_policy: "",
  notes: "",
};

const CURRENCIES = [
  ["UGX", "UGX — Ugandan Shilling"],
  ["KES", "KES — Kenyan Shilling"],
  ["TZS", "TZS — Tanzanian Shilling"],
  ["RWF", "RWF — Rwandan Franc"],
  ["USD", "USD — US Dollar"],
];

// Languages Sunbird AI can transcribe and translate (ISO 639-3 codes).
const LANGUAGES = [
  ["lug", "Luganda"],
  ["eng", "English"],
  ["swa", "Swahili"],
  ["nyn", "Runyankole"],
  ["cgg", "Rukiga"],
  ["xog", "Lusoga"],
  ["ach", "Acholi"],
  ["teo", "Ateso"],
  ["lgg", "Lugbara"],
  ["laj", "Lango"],
  ["kin", "Kinyarwanda"],
];

/**
 * Business profile, stored on the server. SenteFlow's AI reads it on every
 * WhatsApp message (name, type, credit policy, notes), and the currency is
 * used for amounts the owner doesn't label and in replies/briefings.
 */
export default function BusinessProfile({ orgId }) {
  const [form, setForm] = useState(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState(""); // "", "saving", "saved"
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getBusinessProfile(orgId)
      .then((p) => {
        if (cancelled) return;
        setForm({ ...EMPTY, ...Object.fromEntries(Object.entries(p || {}).filter(([, v]) => v != null)) });
        setLoaded(true);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e.message || "Could not load your business profile");
        setLoaded(true);
      });
    return () => { cancelled = true; };
  }, [orgId]);

  function set(field) {
    return (e) => {
      setForm((f) => ({ ...f, [field]: e.target.value }));
      setStatus("");
      setError("");
    };
  }

  async function save() {
    setStatus("saving");
    setError("");
    try {
      const saved = await saveBusinessProfile(orgId, {
        name: form.name,
        business_type: form.business_type,
        location: form.location,
        currency: form.currency,
        language: form.language,
        reply_language: form.reply_language,
        credit_policy: form.credit_policy,
        notes: form.notes,
      });
      setForm((f) => ({ ...f, ...saved }));
      setStatus("saved");
      setTimeout(() => setStatus((v) => (v === "saved" ? "" : v)), 2500);
    } catch (e) {
      setStatus("");
      setError(e.message || "Could not save your business profile");
    }
  }

  const fieldStyle = { ...s.input, width: "100%", marginTop: 8 };
  const labelStyle = { color: C.textDim, fontSize: 13, fontWeight: 600, display: "block", marginTop: 16 };
  const hintStyle = { color: C.textMuted, fontSize: 12, fontWeight: 400, marginTop: 4 };

  return (
    <div style={s.section}>
      <p style={s.sectionTitle}>Business profile</p>
      <p style={{ color: C.textMuted, fontSize: 12, margin: "4px 0 0", lineHeight: 1.5 }}>
        SenteFlow reads this whenever you message it, so it understands your business.
      </p>

      <label style={labelStyle}>
        Business name
        <input style={fieldStyle} placeholder="Kamu General Supplies" value={form.name} onChange={set("name")} disabled={!loaded} />
      </label>
      <label style={labelStyle}>
        What you sell
        <input style={fieldStyle} placeholder="Hardware and building materials" value={form.business_type} onChange={set("business_type")} disabled={!loaded} />
      </label>
      <label style={labelStyle}>
        Location
        <input style={fieldStyle} placeholder="Kawempe, Kampala" value={form.location} onChange={set("location")} disabled={!loaded} />
      </label>
      <label style={labelStyle}>
        Default currency
        <select style={{ ...s.select, width: "100%", marginTop: 8 }} value={form.currency} onChange={set("currency")} disabled={!loaded}>
          {CURRENCIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <div style={hintStyle}>Used when you don't say a currency, and in replies and briefings.</div>
      </label>
      <label style={labelStyle}>
        Language you speak
        <select style={{ ...s.select, width: "100%", marginTop: 8 }} value={form.language} onChange={set("language")} disabled={!loaded}>
          {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <div style={hintStyle}>Voice notes are transcribed in this language. Typed messages in any of these languages are understood.</div>
      </label>
      <label style={labelStyle}>
        Replies
        <select style={{ ...s.select, width: "100%", marginTop: 8 }} value={form.reply_language} onChange={set("reply_language")} disabled={!loaded}>
          <option value="english">Always in English</option>
          <option value="same">In the language I write in</option>
        </select>
        <div style={hintStyle}>Translated replies are machine translations. If a translation would change a number, SenteFlow replies in English instead.</div>
      </label>
      <label style={labelStyle}>
        Credit policy
        <input style={fieldStyle} placeholder="Regulars can take up to 200k on credit, pay within 2 weeks" value={form.credit_policy} onChange={set("credit_policy")} disabled={!loaded} />
      </label>
      <label style={labelStyle}>
        Anything else SenteFlow should know
        <textarea
          style={{ ...fieldStyle, minHeight: 72, resize: "vertical", fontFamily: "inherit" }}
          placeholder="'Bags' means 50kg cement bags. Mama Brian is Brian's mother, not Brian."
          value={form.notes}
          onChange={set("notes")}
          disabled={!loaded}
        />
      </label>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 16 }}>
        <button style={s.primaryBtn} onClick={save} disabled={!loaded || status === "saving"}>
          {status === "saving" ? "Saving…" : "Save profile"}
        </button>
        {status === "saved" && <span style={{ color: C.green, fontSize: 13, fontWeight: 600 }}>Saved</span>}
      </div>
      {error && <div style={{ color: C.red, fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
