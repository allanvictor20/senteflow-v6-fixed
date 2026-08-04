import { initializeApp } from "firebase/app";
import { getFirestore, collection, onSnapshot, query, orderBy, where } from "firebase/firestore";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut } from "firebase/auth";
import { getStorage } from "firebase/storage";

const firebaseConfig = {
  apiKey: process.env.REACT_APP_FIREBASE_API_KEY,
  authDomain: process.env.REACT_APP_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.REACT_APP_FIREBASE_PROJECT_ID,
  storageBucket: process.env.REACT_APP_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.REACT_APP_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.REACT_APP_FIREBASE_APP_ID,
};

const app = initializeApp(firebaseConfig);

export const db = getFirestore(app);
export const auth = getAuth(app);
export const storage = getStorage(app);
export const googleProvider = new GoogleAuthProvider();

export const signInWithGoogle = () => signInWithPopup(auth, googleProvider);
export const logOut = () => signOut(auth);

// The backend writes every WhatsApp-derived record to `events`; `transactions`
// is the pre-v6 collection and is no longer populated. Subscribing to it left
// the dashboard permanently empty.
export const EVENTS_COLLECTION = "events";

export const subscribeToTransactions = (orgId, status, callback) => {
  const ref = collection(db, "organizations", orgId, EVENTS_COLLECTION);
  const q = status
    ? query(ref, where("status", "==", status), orderBy("created_at", "desc"))
    : query(ref, orderBy("created_at", "desc"));

  return onSnapshot(
    q,
    (snapshot) => {
      const transactions = snapshot.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
      callback(transactions);
    },
    (error) => {
      console.error("Firestore subscription error:", error.code, error.message);
      callback([], error);
    }
  );
};
