/**
 * SenteFlow AI — Shared UI Components
 * ======================================
 * Reusable primitive components used across all features.
 * Each component has a single, clear responsibility.
 */

import { C, s } from "../../utils/theme";
import { isExpenseType, confidenceColor } from "../../utils/format";
import { getTransactionEvidence } from "../../services/api";
import { useState } from "react";

export function ConfidenceDot({ color, size = 8 }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
      }}
    />
  );
}

export function Tag({ children, color }) {
  return (
    <span
      style={{
        background: color + "22",
        color,
        border: `1px solid ${color}44`,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 11,
      }}
    >
      {children}
    </span>
  );
}

export function InfoPill({ label, value }) {
  return (
    <div>
      <div
        style={{
          color: C.textMuted,
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div style={{ color: C.text, fontSize: 13, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

export function EvidenceModal({ evidence, onClose }) {
  return (
    <div style={s.modalOverlay} onClick={onClose}>
      <div style={s.modal} onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 16,
          }}
        >
          <h3 style={{ color: C.white, margin: 0 }}>Transaction Evidence</h3>
          <button style={{ ...s.ghostBtn, padding: "4px 10px" }} aria-label="Close evidence modal" onClick={onClose}>
            ✕
          </button>
        </div>
        {evidence.source_trace ? (
          <div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
              <InfoPill label="File" value={evidence.source_trace.source_file_name} />
              <InfoPill
                label="Session"
                value={evidence.source_trace.upload_session_id?.slice(0, 8) + "…"}
              />
              <InfoPill
                label="Extracted"
                value={evidence.source_trace.extraction_timestamp
                  ?.slice(0, 19)
                  .replace("T", " ")}
              />
              <InfoPill label="Model" value={evidence.source_trace.ai_model} />
            </div>
            {evidence.source_trace.transcript_snippet && (
              <div style={{ ...s.section, background: C.bg }}>
                <h4 style={{ color: C.textDim, fontSize: 12, margin: "0 0 8px" }}>
                  SOURCE SNIPPET
                </h4>
                <p style={{ color: C.textMuted, fontSize: 13, fontStyle: "italic", margin: 0 }}>
                  "{evidence.source_trace.transcript_snippet}"
                </p>
              </div>
            )}
          </div>
        ) : (
          <p style={{ color: C.textMuted, fontSize: 13 }}>
            No source trace available for this transaction.
          </p>
        )}
      </div>
    </div>
  );
}

export function TxnRow({ t, orgId }) {
  const [evidence, setEvidence] = useState(null);
  const expenseType = isExpenseType(t.transaction_type || t.type);
  const confColor = confidenceColor(t.confidence_overall, C);

  async function viewEvidence() {
    try {
      const data = await getTransactionEvidence(orgId, t.id);
      setEvidence(data.data);
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <>
      <div
        style={{
          ...s.card,
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 8,
          padding: "12px 16px",
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 8,
            flexShrink: 0,
            background: expenseType ? C.redDim : C.greenDim,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
            color: expenseType ? C.red : C.green,
          }}
        >
          {expenseType ? "↓" : "↑"}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              color: C.text,
              fontWeight: 600,
              fontSize: 14,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {t.description}
          </div>
          <div
            style={{
              color: C.textMuted,
              fontSize: 12,
              marginTop: 2,
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <span>{t.category}</span>
            {t.payer && <span>· {t.payer}</span>}
            {t.date && <span>· {t.date}</span>}
            {t.confidence_overall && (
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                · <ConfidenceDot color={confColor} size={6} /> {t.confidence_overall}
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <span style={{ color: expenseType ? C.red : C.green, fontWeight: 700 }}>
            {expenseType ? "-" : "+"}
            {t.currency} {Number(t.amount).toLocaleString()}
          </span>
          <button
            style={{ background: "none", border: "none", color: C.textMuted, cursor: "pointer", fontSize: 11, padding: 0 }}
            onClick={viewEvidence}
          >
            🔍 evidence
          </button>
        </div>
      </div>
      {evidence && <EvidenceModal evidence={evidence} onClose={() => setEvidence(null)} />}
    </>
  );
}
