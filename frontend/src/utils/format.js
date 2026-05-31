/** Format a number as UGX currency */
export const fmt = (n) => `UGX ${Number(n || 0).toLocaleString()}`;

/** Determine if a transaction type is an expense/outflow */
export const isExpenseType = (type) =>
  ["expense", "payment", "withdrawal"].includes(type);

/** Map confidence label to color */
export const confidenceColor = (label, C) =>
  ({ high: C.green, medium: C.yellow, low: C.red }[label] || C.textMuted);
