/**
 * SenteFlow AI — API Service
 * ============================
 * Centralized HTTP client for the SenteFlow backend.
 * All API calls go through here — never call fetch() directly in components.
 */

const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

async function handleResponse(res) {
  const json = await res.json();
  if (!res.ok || !json.success) {
    const msg = json.error || json.detail || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.code = json.error_code;
    err.correlationId = json.correlation_id;
    throw err;
  }
  return json;
}

export async function extractFromFile(file, orgId, uploadedBy, invoicePrompt = "") {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("org_id", orgId);
  formData.append("uploaded_by", uploadedBy);
  if (invoicePrompt) formData.append("invoice_prompt", invoicePrompt);

  const res = await fetch(`${API_BASE}/extract`, { method: "POST", body: formData });
  return handleResponse(res);
}

export async function approveTransactions(transactions, orgId, approvedBy, sessionId = "") {
  const res = await fetch(`${API_BASE}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transactions, org_id: orgId, approved_by: approvedBy, session_id: sessionId }),
  });
  return handleResponse(res);
}

export async function getTransactions(orgId, status = null) {
  const url = new URL(`${API_BASE}/transactions/${orgId}`);
  if (status) url.searchParams.set("status", status);
  return handleResponse(await fetch(url));
}

export async function getOrgSummary(orgId) {
  const envelope = await handleResponse(await fetch(`${API_BASE}/summary/${orgId}`));
  return envelope.data;
}

export async function getTransactionEvidence(orgId, transactionId) {
  return handleResponse(await fetch(`${API_BASE}/audit/${orgId}/${transactionId}`));
}

export async function getWhatsAppStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/webhooks/whatsapp/status`);
    if (!res.ok) return { configured: false, status: "error" };
    return res.json();
  } catch {
    return { configured: false, status: "offline" };
  }
}

export async function getCustomers(orgId) {
  const envelope = await handleResponse(await fetch(`${API_BASE}/customers/${orgId}`));
  return envelope.data;
}

export async function getOrders(orgId, filters = {}) {
  const url = new URL(`${API_BASE}/orders/${orgId}`);
  Object.entries(filters).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
  });
  const envelope = await handleResponse(await fetch(url));
  return envelope.data;
}

export async function getMediaAssets(orgId) {
  const envelope = await handleResponse(await fetch(`${API_BASE}/media-assets/${orgId}`));
  return envelope.data;
}

export async function askBusinessAssistant(orgId, question, senderId = "") {
  const res = await fetch(`${API_BASE}/assistant/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ org_id: orgId, question, sender_id: senderId }),
  });
  const envelope = await handleResponse(res);
  return envelope.data;
}
