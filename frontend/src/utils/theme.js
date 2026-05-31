/**
 * SenteFlow AI — Design Tokens
 * ==============================
 * Single source of truth for all colors, spacing, and visual constants.
 * Import from here rather than hardcoding values in components.
 */

export const C = {
  bg:           "#090c14",
  surface:      "#111623",
  surfaceHigh:  "#1a2035",
  border:       "#222b45",
  borderLight:  "#2e3a5c",
  accent:       "#5b6af5",
  accentGlow:   "#5b6af533",
  accentLight:  "#818cf8",
  green:        "#10b981",
  greenDim:     "#10b98122",
  red:          "#f43f5e",
  redDim:       "#f43f5e22",
  yellow:       "#f59e0b",
  yellowDim:    "#f59e0b22",
  purple:       "#8b5cf6",
  text:         "#e2e8f0",
  textMuted:    "#64748b",
  textDim:      "#94a3b8",
  white:        "#ffffff",
  darkText:     "#1f2937",
};

export const globalCSS = `
  * { box-sizing: border-box; }
  body { margin: 0; }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #2e3a5c; border-radius: 3px; }
  button:focus-visible { outline: 2px solid #5b6af5; outline-offset: 2px; }
  input:focus-visible { outline: 2px solid #5b6af5; outline-offset: 2px; }
`;

export const s = {
  app:           { display: "flex", height: "100vh", background: C.bg, color: C.text, fontFamily: "'Inter', system-ui, -apple-system, sans-serif", overflow: "hidden" },
  sidebar:       { width: 228, background: C.surface, borderRight: `1px solid ${C.border}`, display: "flex", flexDirection: "column", flexShrink: 0 },
  logo:          { display: "flex", alignItems: "center", gap: 12, padding: "20px 20px 16px", borderBottom: `1px solid ${C.border}` },
  logoIcon:      { width: 36, height: 36, borderRadius: 10, background: `linear-gradient(135deg, ${C.accent}, ${C.purple})`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, flexShrink: 0 },
  navBtn:        { display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 20px", background: "none", border: "none", color: C.textMuted, cursor: "pointer", fontSize: 14, textAlign: "left", borderLeft: "2px solid transparent", transition: "all 0.15s" },
  navBtnActive:  { background: C.accentGlow, color: C.accentLight, borderLeftColor: C.accent },
  badge:         { background: C.accent, color: C.white, borderRadius: 10, fontSize: 11, padding: "1px 7px", fontWeight: 700 },
  sidebarFooter: { display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderTop: `1px solid ${C.border}`, marginTop: "auto" },
  avatar:        { width: 32, height: 32, borderRadius: "50%", objectFit: "cover" },
  logoutBtn:     { background: "none", border: "none", color: C.textMuted, cursor: "pointer", fontSize: 16, padding: 4, borderRadius: 6 },
  main:          { flex: 1, overflowY: "auto", background: C.bg },
  mobileHeader:  { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", background: C.surface, borderBottom: `1px solid ${C.border}`, flexShrink: 0 },
  mobileNav:     { position: "fixed", bottom: 0, left: 0, right: 0, background: C.surface, borderTop: `1px solid ${C.border}`, display: "flex", zIndex: 100 },
  mobileNavBtn:  { flex: 1, background: "none", border: "none", color: C.textMuted, cursor: "pointer", padding: "8px 4px 10px", display: "flex", flexDirection: "column", alignItems: "center", fontSize: 10, gap: 2, position: "relative" },
  mobileNavBtnActive: { color: C.accentLight },
  page:          { padding: "20px 16px", maxWidth: 960, margin: "0 auto" },
  pageHeader:    { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 12 },
  pageTitle:     { color: C.white, fontSize: 20, fontWeight: 800, margin: 0 },
  pageSubtitle:  { color: C.textMuted, fontSize: 13, margin: "4px 0 0" },
  cardGrid4:     { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 14, marginBottom: 20 },
  card:          { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 16 },
  section:       { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 20px", marginBottom: 16 },
  sectionTitle:  { color: C.textDim, fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 14px" },
  primaryBtn:    { background: C.accent, color: C.white, border: "none", borderRadius: 8, padding: "9px 18px", cursor: "pointer", fontWeight: 600, fontSize: 14, display: "flex", alignItems: "center", gap: 4, transition: "opacity 0.15s" },
  ghostBtn:      { background: "none", color: C.textDim, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 18px", cursor: "pointer", fontSize: 14 },
  dropZone:      { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", border: "2px dashed", borderRadius: 16, padding: "56px 32px", marginBottom: 20, transition: "all 0.2s", textAlign: "center", minHeight: 220 },
  toast:         { position: "fixed", top: 20, right: 20, color: C.white, padding: "12px 20px", borderRadius: 10, fontWeight: 600, fontSize: 13, zIndex: 9999, boxShadow: "0 8px 32px rgba(0,0,0,0.5)", display: "flex", alignItems: "center" },
  input:         { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 14px", color: C.text, fontSize: 14, outline: "none" },
  select:        { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 14px", color: C.text, fontSize: 14, outline: "none" },
  loginPage:     { display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", background: C.bg },
  loginCard:     { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 20, padding: "40px 36px", width: 380, position: "relative", overflow: "hidden" },
  loginGlow:     { position: "absolute", top: -80, left: "50%", transform: "translateX(-50%)", width: 300, height: 300, borderRadius: "50%", background: `radial-gradient(circle, ${C.accentGlow} 0%, transparent 70%)`, pointerEvents: "none" },
  googleBtn:     { display: "flex", alignItems: "center", justifyContent: "center", width: "100%", padding: "13px 0", background: C.white, color: C.darkText, border: "none", borderRadius: 10, cursor: "pointer", fontWeight: 700, fontSize: 14 },
  modalOverlay:  { position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9998, padding: 20 },
  modal:         { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 16, padding: 24, width: "100%", maxWidth: 560, maxHeight: "80vh", overflowY: "auto" },
};
