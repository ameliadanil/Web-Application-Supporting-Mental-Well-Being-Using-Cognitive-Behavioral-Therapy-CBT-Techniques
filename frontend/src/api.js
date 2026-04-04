// src/api.js
const BASE = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export async function startBreathing(technique = "box", lang = "en") {
  const res = await fetch(`${BASE}/breathing/session/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ technique, lang })
  });
  if (!res.ok) throw new Error("startBreathing failed");
  return res.json();
}

export async function tickBreathing(payload) {
  try {
    await fetch(`${BASE}/breathing/session/tick`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch { /* nic – fallback działa lokalnie */ }
}
