/**
 * SenteFlow AI — API Service
 * ============================
 * Centralized HTTP client for the SenteFlow backend.
 * All API calls go through here — never call fetch() directly in components.
 *
 * Every request carries the caller's Firebase ID token. The backend guards
 * every route with verify_firebase_token, so a request without the
 * Authorization header is answered with 401 before it reaches a handler.
 */

import { auth } from "../firebase/config";

const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

/** Current user's Firebase ID token, or null when signed out. */
async function getIdToken() {
  const user = auth.currentUser;
  if (!user) return null;
  try {
    return await user.getIdToken();
  } catch (err) {
    console.error("Failed to get Firebase ID token:", err);
    return null;
  }
}

/** fetch() wrapper that attaches auth and normalises errors. */
async function apiFetch(url, options = {}) {
  const token = await getIdToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

async function handleResponse(res) {
  let json;
  try {
    json = await res.json();
  } catch {
    throw new Error(`HTTP ${res.status} — response was not JSON`);
  }

  // Legacy routes wrap responses in {success, data, error}. The newer /api
  // routers return bare objects, so treat a missing `success` flag on a 2xx
  // response as success rather than as a failure.
  const isEnvelope = typeof json?.success === "boolean";
  if (!res.ok || (isEnvelope && !json.success)) {
    const msg = json?.error || json?.detail || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.code = json?.error_code;
    err.correlationId = json?.correlation_id;
    throw err;
  }
  return json;
}

/** Unwrap {success, data} envelopes; pass bare payloads straight through. */
function unwrap(json) {
  return typeof json?.success === "boolean" && "data" in json ? json.data : json;
}

function withParams(path, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  return url;
}

export async function extractFromFile(file, orgId, uploadedBy, invoicePrompt = "") {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("org_id", orgId);
  formData.append("uploaded_by", uploadedBy);
  if (invoicePrompt) formData.append("invoice_prompt", invoicePrompt);

  // Content-Type is intentionally omitted: the browser must set the multipart
  // boundary itself.
  const res = await apiFetch(`${API_BASE}/extract`, { method: "POST", body: formData });
  return handleResponse(res);
}

export async function approveTransactions(transactions, orgId, approvedBy, sessionId = "") {
  const res = await apiFetch(`${API_BASE}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transactions, org_id: orgId, approved_by: approvedBy, session_id: sessionId }),
  });
  return handleResponse(res);
}

export async function getTransactions(orgId, status = null) {
  const url = withParams(`/transactions/${orgId}`, { status });
  return handleResponse(await apiFetch(url));
}

export async function getOrgSummary(orgId) {
  return unwrap(await handleResponse(await apiFetch(`${API_BASE}/summary/${orgId}`)));
}

export async function getTransactionEvidence(orgId, transactionId) {
  return handleResponse(await apiFetch(`${API_BASE}/audit/${orgId}/${transactionId}`));
}

export async function getWhatsAppStatus() {
  try {
    const res = await apiFetch(`${API_BASE}/api/webhooks/whatsapp/status`);
    if (!res.ok) return { configured: false, status: "error" };
    return res.json();
  } catch {
    return { configured: false, status: "offline" };
  }
}

// ── /api routers: org_id is a query parameter, not a path segment ────────────

export async function getCustomers(orgId, limit = 100) {
  const url = withParams("/api/customers", { org_id: orgId, limit });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function getCustomer(orgId, customerId) {
  const url = withParams(`/api/customers/${customerId}`, { org_id: orgId });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function getOrders(orgId, filters = {}) {
  const url = withParams("/api/orders", { org_id: orgId, ...filters });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function getTasks(orgId, limit = 100) {
  const url = withParams("/api/tasks", { org_id: orgId, limit });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function getInsights(orgId) {
  const url = withParams("/api/insights", { org_id: orgId });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function getMediaAssets(orgId, limit = 50) {
  const url = withParams(`/media-assets/${orgId}`, { limit });
  return unwrap(await handleResponse(await apiFetch(url)));
}

export async function askBusinessAssistant(orgId, question, senderId = "") {
  const res = await apiFetch(`${API_BASE}/assistant/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ org_id: orgId, question, sender_id: senderId }),
  });
  return unwrap(await handleResponse(res));
}
