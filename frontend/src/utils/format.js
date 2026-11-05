/** Format a number as UGX currency */
export const fmt = (n) => `UGX ${Number(n || 0).toLocaleString()}`;

const MONEY_IN = ["payment", "payment_received", "income", "contribution", "loan_repayment"];
const MONEY_OUT = ["expense", "expense_recorded", "withdrawal"];
const OWED = ["debt_created", "payment_promise", "loan"];

/**
 * How an entry affects the money position, for colour and sign:
 *   "in"   — money received (+, green)
 *   "out"  — money spent (−, red)
 *   "owed" — a customer owes the business; no cash moved yet (amber, no sign)
 * In SenteFlow "payment" means a payment RECEIVED from a customer.
 */
export const moneyDirection = (type) =>
  MONEY_IN.includes(type) ? "in" : MONEY_OUT.includes(type) ? "out" : OWED.includes(type) ? "owed" : "other";

/** Determine if a transaction type is an expense/outflow */
export const isExpenseType = (type) => moneyDirection(type) === "out";

/** True when an ISO timestamp falls on today's date in the viewer's time zone. */
export const isToday = (iso) => {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
};

/** "+UGX 50,000", "−UGX 40,000" or "owes UGX 150,000" */
export const signedAmount = (t) => {
  const dir = moneyDirection(t.transaction_type || t.type || t.event_type);
  const amount = `${t.currency || "UGX"} ${Number(t.amount || 0).toLocaleString()}`;
  if (dir === "in") return `+${amount}`;
  if (dir === "out") return `−${amount}`;
  if (dir === "owed") return `owes ${amount}`;
  return amount;
};

/** Map confidence label to color */
export const confidenceColor = (label, C) =>
  ({ high: C.green, medium: C.yellow, low: C.red }[label] || C.textMuted);
