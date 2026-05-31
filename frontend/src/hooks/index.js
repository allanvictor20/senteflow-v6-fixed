/**
 * SenteFlow AI — Custom Hooks
 * ============================
 * Shared stateful logic extracted from components.
 * Components should use hooks; hooks should not render anything.
 */

import { useState, useEffect } from "react";
import { onAuthStateChanged } from "firebase/auth";
import { auth, subscribeToTransactions } from "../firebase/config";
import { getOrgSummary } from "../services/api";

/** Detect mobile viewport */
export function useIsMobile() {
  const [mobile, setMobile] = useState(window.innerWidth <= 640);
  useEffect(() => {
    const handler = () => setMobile(window.innerWidth <= 640);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);
  return mobile;
}

/** Firebase auth state */
export function useAuth() {
  const [user, setUser] = useState(null);
  useEffect(() => onAuthStateChanged(auth, setUser), []);
  return user;
}

/** Live Firestore transaction subscription */
export function useTransactions(orgId, status = "approved") {
  const [transactions, setTransactions] = useState([]);
  const [feedError, setFeedError] = useState(false);
  useEffect(() => {
    setFeedError(false);
    const unsub = subscribeToTransactions(
      orgId,
      status,
      (data, error) => {
        if (error) {
          setFeedError(true);
        } else {
          setTransactions(data);
          setFeedError(false);
        }
      }
    );
    return unsub;
  }, [orgId, status]);
  return { transactions, feedError };
}

/** Financial summary, refreshed whenever transactions change */
export function useSummary(orgId, transactions) {
  const [summary, setSummary] = useState({
    total_income: 0, total_expenses: 0, balance: 0,
    categories: {}, confidence_distribution: {},
    members_paid: 0, members_pending: 0, pending_amount: 0,
  });
  const [summaryError, setSummaryError] = useState(false);

  // Build a fingerprint that changes whenever a transaction's status changes,
  // not just when the count changes. Joining id+status for the first 50 docs
  // is cheap and catches the most common mutation (pending → approved).
  const transactionFingerprint = transactions
    .slice(0, 50)
    .map((t) => `${t.id}:${t.status || ""}`)
    .join("|");

  useEffect(() => {
    setSummaryError(false);
    getOrgSummary(orgId)
      .then(setSummary)
      .catch(() => setSummaryError(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, transactionFingerprint]);
  return { summary, summaryError };
}

/** Derive anomaly alerts from transactions */
export function useAlerts(transactions) {
  const [alerts, setAlerts] = useState([]);
  useEffect(() => {
    const newAlerts = [];
    const amounts = transactions.map((t) => t.amount).filter(Boolean);
    const avgAmount = amounts.length
      ? amounts.reduce((a, b) => a + b, 0) / amounts.length
      : 0;

    transactions.slice(0, 20).forEach((t) => {
      if (t.amount > avgAmount * 4 && avgAmount > 0) {
        newAlerts.push({
          id: t.id,
          type: "suspicious",
          msg: `Large transaction: UGX ${Number(t.amount).toLocaleString()} by ${t.payer || "unknown"} — ${(t.amount / avgAmount).toFixed(1)}× above average`,
        });
      }
      if (t.anomalies?.length > 0) {
        t.anomalies.forEach((a) => newAlerts.push({ id: t.id + a, type: "flag", msg: a }));
      }
    });
    setAlerts(newAlerts.slice(0, 5));
  }, [transactions]);
  return alerts;
}
