// config.ts
// Central config — API base URL and other constants.
// In development, Vite proxies /api to localhost:8001 automatically.
// In production, set VITE_API_URL to your deployed backend URL.

export const API_BASE = import.meta.env.VITE_API_URL || '';
// Empty string means "same origin" — works with the Vite proxy in dev
// and with nginx serving both frontend and backend in production.