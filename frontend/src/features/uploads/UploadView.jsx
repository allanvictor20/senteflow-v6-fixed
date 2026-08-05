import { useState } from "react";
import { C, s } from "../../utils/theme";

export default function UploadView({ fileRef, uploading, onFileChange }) {
  const [dragging, setDragging] = useState(false);
  const [invoicePrompt, setInvoicePrompt] = useState("");

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && fileRef.current) {
      const dt = new DataTransfer();
      dt.items.add(file);
      fileRef.current.files = dt.files;
      onFileChange({ target: { files: dt.files } }, invoicePrompt);
    }
  }

  const fileTypes = [
    { icon: "🎙", label: "Voice Notes", desc: "MP3, WAV, OGG, M4A — WhatsApp audio, meeting recordings" },
    { icon: "📱", label: "MoMo Screenshots", desc: "MTN MoMo, Airtel Money, M-Pesa receipts" },
    { icon: "📋", label: "Handwritten Ledgers", desc: "Photograph your contribution book" },
    { icon: "💬", label: "WhatsApp Messages", desc: "Paste text from treasurer chats" },
  ];

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.pageTitle}>Upload Records</h2>
          <p style={s.pageSubtitle}>Upload any file — AI extracts structured contributions automatically</p>
        </div>
      </div>

      <div
        style={{ ...s.dropZone, borderColor: dragging ? C.accent : uploading ? C.yellow : C.borderLight, background: dragging ? C.accentGlow : uploading ? C.yellowDim : C.surface }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        {uploading ? (
          <>
            <div className="spin" style={{ fontSize: 40, marginBottom: 12 }}>⟳</div>
            <p style={{ color: C.yellow, fontWeight: 700, fontSize: 16, margin: 0 }}>Extracting transactions…</p>
            <p style={{ color: C.textMuted, fontSize: 13, marginTop: 6 }}>Groq AI is processing your file</p>
          </>
        ) : (
          <>
            <div style={{ fontSize: 40, marginBottom: 12 }}>📁</div>
            <p style={{ color: C.text, fontWeight: 700, fontSize: 16, margin: "0 0 6px" }}>
              {dragging ? "Drop to upload" : "Drop any file here"}
            </p>
            <p style={{ color: C.textMuted, fontSize: 13, marginBottom: 20 }}>
              voice notes, receipts, screenshots, photos of ledgers
            </p>
            <button style={s.primaryBtn} onClick={() => fileRef.current?.click()}>
              Browse Files
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*,image/*,.pdf,.txt,.doc,.docx"
              style={{ display: "none" }}
              onChange={(e) => onFileChange(e, invoicePrompt)}
            />
          </>
        )}
      </div>

      <div style={{ ...s.section, marginBottom: 24 }}>
        <h3 style={s.sectionTitle}>Optional: Ask a specific question (for invoices)</h3>
        <input
          style={{ ...s.input, width: "100%", boxSizing: "border-box" }}
          placeholder='e.g. "What is the total amount?" or "Who is the sender?"'
          value={invoicePrompt}
          onChange={(e) => setInvoicePrompt(e.target.value)}
        />
      </div>

      <div style={s.cardGrid4}>
        {fileTypes.map((ft) => (
          <div key={ft.label} style={{ ...s.card, textAlign: "center" }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>{ft.icon}</div>
            <div style={{ color: C.text, fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{ft.label}</div>
            <div style={{ color: C.textMuted, fontSize: 11 }}>{ft.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
