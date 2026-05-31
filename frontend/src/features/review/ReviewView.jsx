import { useState, useEffect } from "react";
import { C, s } from "../../utils/theme";
import { confidenceColor } from "../../utils/format";
import { ConfidenceDot, Tag, InfoPill } from "../../components/ui";

export default function ReviewView({ result, onApprove, onDiscard }) {
  const [selected, setSelected] = useState(new Set());
  const [expandedIdx, setExpandedIdx] = useState(null);

  useEffect(() => {
    const items = result?.events || result?.transactions || [];
    if (items.length) {
      const preSelected = new Set(
        items.map((_, i) => i).filter((i) => items[i].confidence_label !== "low")
      );
      setSelected(preSelected);
    }
  }, [result]);

  if (!result) {
    return (
      <div style={{ ...s.page, textAlign: "center", paddingTop: 80 }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>◎</div>
        <h2 style={{ color: C.text, margin: "0 0 8px" }}>No Pending Events</h2>
        <p style={{ color: C.textMuted, margin: 0 }}>Upload a file first to review extracted events.</p>
      </div>
    );
  }

  const events = result.events || result.transactions || [];
  const selectedEvents = events.filter((_, i) => selected.has(i));

  function toggle(idx) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  }

  function toggleAll() {
    setSelected(
      selected.size === events.length
        ? new Set()
        : new Set(events.map((_, i) => i))
    );
  }

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Review Events</h2>
          <p style={s.pageSubtitle}>{selected.size} of {events.length} events selected for approval</p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button style={s.ghostBtn} onClick={onDiscard}>Discard</button>
          <button
            style={{ ...s.primaryBtn, opacity: selected.size === 0 ? 0.5 : 1 }}
            onClick={() => onApprove(selectedEvents)}
            disabled={selected.size === 0}
          >
            ✓ Approve {selected.size > 0 ? `(${selected.size})` : ""}
          </button>
        </div>
      </div>

      <div style={{ ...s.section, marginBottom: 16, background: C.accentGlow, borderColor: C.accent + "44" }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          <InfoPill label="Input type" value={result.input_type?.replace("_", " ")} />
          <InfoPill label="Language" value={result.language || "en"} />
          <InfoPill label="Found" value={`${events.length} event${events.length !== 1 ? "s" : ""}`} />
        </div>
        {result.summary && <p style={{ color: C.textDim, fontSize: 13, marginTop: 10, marginBottom: 0 }}>{result.summary}</p>}
      </div>

      {result.anomalies?.length > 0 && (
        <div style={{ ...s.section, background: C.yellowDim, borderColor: C.yellow + "44", marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ color: C.yellow, fontSize: 16 }}>⚠</span>
            <h3 style={{ ...s.sectionTitle, margin: 0, color: C.yellow }}>Anomalies Detected</h3>
          </div>
          {result.anomalies.map((a, i) => (
            <div key={i} style={{ color: C.textDim, fontSize: 13, padding: "4px 0", borderBottom: i < result.anomalies.length - 1 ? `1px solid ${C.border}` : "none" }}>
              {a}
            </div>
          ))}
        </div>
      )}

      {result.raw_transcript && (
        <details style={{ ...s.section, marginBottom: 16, cursor: "pointer" }}>
          <summary style={{ color: C.textDim, fontSize: 13, fontWeight: 600 }}>📝 Audio Transcript — click to expand</summary>
          <p style={{ color: C.textMuted, fontSize: 13, marginTop: 10, marginBottom: 0, lineHeight: 1.6, fontStyle: "italic" }}>"{result.raw_transcript}"</p>
        </details>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <button style={s.ghostBtn} onClick={toggleAll}>
          {selected.size === events.length ? "Deselect all" : "Select all"}
        </button>
        <span style={{ color: C.textMuted, fontSize: 12 }}>Click an event to select/deselect</span>
      </div>

      {events.map((evt, i) => {
        const checked = selected.has(i);
        const type = evt.event_type || evt.transaction_type || evt.type || "business_note";
        const isExpense = ["expense_recorded", "expense", "payment", "withdrawal"].includes(type);
        const confColor = confidenceColor(evt.confidence_label, C);
        const confScore = evt.confidence_score != null ? `${Math.round(evt.confidence_score * 100)}%` : "";
        const isExpanded = expandedIdx === i;

        return (
          <div key={i} style={{ ...s.card, marginBottom: 10, border: `1px solid ${checked ? C.accent + "66" : C.border}`, background: checked ? C.accentGlow : C.surface, transition: "all 0.15s" }}>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-start", cursor: "pointer" }} onClick={() => toggle(i)}>
              <div style={{ width: 20, height: 20, borderRadius: 5, border: `2px solid ${checked ? C.accent : C.border}`, background: checked ? C.accent : "transparent", flexShrink: 0, marginTop: 2, display: "flex", alignItems: "center", justifyContent: "center" }}>
                {checked && <span style={{ color: C.white, fontSize: 11 }}>✓</span>}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ color: C.text, fontWeight: 600, fontSize: 14 }}>{evt.description}</span>
                  <span style={{ color: isExpense ? C.red : C.green, fontWeight: 800, fontSize: 15, whiteSpace: "nowrap" }}>
                    {isExpense ? "-" : "+"}{evt.currency || "UGX"} {Number(evt.amount || 0).toLocaleString()}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                  <Tag color={C.accent}>{type}</Tag>
                  <Tag color={C.surfaceHigh}>{evt.category}</Tag>
                  {evt.payer && <Tag color={C.surfaceHigh}>From: {evt.payer}</Tag>}
                  {evt.date && <Tag color={C.surfaceHigh}>{evt.date}</Tag>}
                  <span style={{ background: confColor + "22", color: confColor, border: `1px solid ${confColor}44`, borderRadius: 4, padding: "2px 8px", fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}>
                    <ConfidenceDot color={confColor} size={6} />
                    {evt.confidence_label || "unknown"} {confScore}
                  </span>
                </div>
              </div>
            </div>

            {evt.source_trace && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${C.border}` }}>
                <button style={{ background: "none", border: "none", color: C.textMuted, cursor: "pointer", fontSize: 12, padding: 0 }} onClick={() => setExpandedIdx(isExpanded ? null : i)}>
                  {isExpanded ? "▲ Hide" : "▼ Show"} source evidence
                </button>
                {isExpanded && (
                  <div style={{ marginTop: 8, padding: "10px 12px", background: C.bg, borderRadius: 8, border: `1px solid ${C.border}` }}>
                    <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: evt.source_trace.transcript_snippet ? 8 : 0 }}>
                      <InfoPill label="Source file" value={evt.source_trace.source_file_name} />
                      <InfoPill label="Session" value={evt.source_trace.upload_session_id?.slice(0, 8) + "…"} />
                      <InfoPill label="Model" value={evt.source_trace.ai_model} />
                    </div>
                    {evt.source_trace.transcript_snippet && (
                      <div style={{ marginTop: 8, color: C.textMuted, fontSize: 12, fontStyle: "italic", lineHeight: 1.5 }}>"{evt.source_trace.transcript_snippet}"</div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
