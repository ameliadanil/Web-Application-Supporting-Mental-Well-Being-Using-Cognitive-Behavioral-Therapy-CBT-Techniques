import React, { useEffect, useState } from "react";
import axios from "axios";

export default function SettingsPanel({ sessionId, lang, setLang, theme, setTheme }) {
  const [exporting, setExporting] = useState(false);
  const API = "http://localhost:8000"; // 🔧 zmień, jeśli backend ma inny adres

  // zapamiętuj ustawienia lokalnie
  useEffect(() => {
    localStorage.setItem("lang", lang);
    localStorage.setItem("theme", theme);
  }, [lang, theme]);

  // eksport CSV
  const exportSession = async (format = "csv") => {
    if (!sessionId) {
      alert("No active session to export!");
      return;
    }
    try {
      setExporting(true);
      const res = await axios.get(`${API}/export/${sessionId}?format=${format}`, {
        responseType: format === "csv" ? "blob" : "json",
      });

      if (format === "csv") {
        const blob = new Blob([res.data], { type: "text/csv" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `session_${sessionId}.csv`;
        a.click();
        window.URL.revokeObjectURL(url);
      } else {
        const json = JSON.stringify(res.data, null, 2);
        const blob = new Blob([json], { type: "application/json" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `session_${sessionId}.json`;
        a.click();
        window.URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error(err);
      alert("Export failed!");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={`settings-panel ${theme === "dark" ? "dark" : "light"}`}>
      <h3>⚙️ Settings</h3>

      <div className="setting-group">
        <label>🌐 Language:</label>
        <select value={lang} onChange={(e) => setLang(e.target.value)}>
          <option value="en">English</option>
          <option value="pl">Polski</option>
        </select>
      </div>

      <div className="setting-group">
        <label>🎨 Theme:</label>
        <select value={theme} onChange={(e) => setTheme(e.target.value)}>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </div>

      <div className="setting-group">
        <label>💾 Export:</label>
        <button disabled={exporting} onClick={() => exportSession("csv")}>
          {exporting ? "Exporting..." : "Download CSV"}
        </button>
        <button disabled={exporting} onClick={() => exportSession("json")}>
          {exporting ? "Exporting..." : "Download JSON"}
        </button>
      </div>
    </div>
  );
}
