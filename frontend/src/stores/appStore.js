/**
 * SenteFlow AI — App Store
 * =========================
 * Central state store using React Context + useReducer pattern.
 * Replaces prop-drilling for deep component trees.
 *
 * Covers:
 *  - Current active view
 *  - Pending extraction result (awaiting review)
 *  - Global notification/toast
 *  - Upload in-progress state
 */

import { createContext, useContext, useReducer } from "react";

// ─── State Shape ──────────────────────────────────────────────────────────────

const initialState = {
  activeView: "dashboard",
  extractionResult: null,
  uploading: false,
  notification: null,
};

// ─── Actions ──────────────────────────────────────────────────────────────────

const actions = {
  SET_VIEW: "SET_VIEW",
  SET_EXTRACTION_RESULT: "SET_EXTRACTION_RESULT",
  CLEAR_EXTRACTION_RESULT: "CLEAR_EXTRACTION_RESULT",
  SET_UPLOADING: "SET_UPLOADING",
  NOTIFY: "NOTIFY",
  CLEAR_NOTIFICATION: "CLEAR_NOTIFICATION",
};

// ─── Reducer ─────────────────────────────────────────────────────────────────

function reducer(state, action) {
  switch (action.type) {
    case actions.SET_VIEW:
      return { ...state, activeView: action.payload };
    case actions.SET_EXTRACTION_RESULT:
      return { ...state, extractionResult: action.payload };
    case actions.CLEAR_EXTRACTION_RESULT:
      return { ...state, extractionResult: null };
    case actions.SET_UPLOADING:
      return { ...state, uploading: action.payload };
    case actions.NOTIFY:
      return { ...state, notification: action.payload };
    case actions.CLEAR_NOTIFICATION:
      return { ...state, notification: null };
    default:
      return state;
  }
}

// ─── Context ──────────────────────────────────────────────────────────────────

const AppStateContext = createContext(null);
const AppDispatchContext = createContext(null);

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <AppStateContext.Provider value={state}>
      <AppDispatchContext.Provider value={dispatch}>
        {children}
      </AppDispatchContext.Provider>
    </AppStateContext.Provider>
  );
}

// ─── Hooks ────────────────────────────────────────────────────────────────────

export function useAppState() {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be inside AppProvider");
  return ctx;
}

export function useAppDispatch() {
  const ctx = useContext(AppDispatchContext);
  if (!ctx) throw new Error("useAppDispatch must be inside AppProvider");
  return ctx;
}

// ─── Action Creators ──────────────────────────────────────────────────────────

export const setView = (view) => ({ type: actions.SET_VIEW, payload: view });
export const setExtractionResult = (result) => ({
  type: actions.SET_EXTRACTION_RESULT,
  payload: result,
});
export const clearExtractionResult = () => ({ type: actions.CLEAR_EXTRACTION_RESULT });
export const setUploading = (val) => ({ type: actions.SET_UPLOADING, payload: val });
export const notify = (msg, msgType = "success") => ({
  type: actions.NOTIFY,
  payload: { msg, type: msgType },
});
export const clearNotification = () => ({ type: actions.CLEAR_NOTIFICATION });
