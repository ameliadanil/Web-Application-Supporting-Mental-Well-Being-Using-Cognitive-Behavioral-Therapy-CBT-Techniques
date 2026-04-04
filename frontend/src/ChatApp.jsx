import React, { useState, useEffect } from "react";
import SettingsPanel from "./components/SettingsPanel";
import axios from "axios";

export default function ChatApp() {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [lang, setLang] = useState(localStorage.getItem("lang") || "en");
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "light");
  const [input, setInput] = useState("");
  const API = "http://localhost:8000";

  const startSession = async () => {
    const res = await axios.post(`${API}/session`, { user_name: "Anna", lang });
    setSessionId(res.data.session_id);
    setMessages([{ role: "bot", text: res.data.message }]);
  };

  const sendMessage = async () => {
    if (!input.trim()) return;
    const res = await axios.post(`${API}/message`, {
      session_id: sessionId,
      text: input,
    });
    setMessages((prev) => [
      ...prev,
      { role: "user", text: input },
      { role: "bot", text: res.data.reply },
    ]);
    setInput("");
  };

  return (
    <div className={`app-container ${theme}`}>
      <SettingsPanel
        sessionId={sessionId}
        lang={lang}
        setLang={setLang}
        theme={theme}
        setTheme={setTheme}
      />

      <div className="chat-window">
        {!sessionId ? (
          <div className="start-screen">
            <h2>AI-CBT Companion</h2>
            <button onClick={startSession}>Start Session</button>
          </div>
        ) : (
          <>
            <div className="messages">
              {messages.map((m, i) => (
                <div key={i} className={m.role}>
                  {m.text}
                </div>
              ))}
            </div>
            <div className="input-bar">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                placeholder="Type your message..."
              />
              <button onClick={sendMessage}>Send</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
