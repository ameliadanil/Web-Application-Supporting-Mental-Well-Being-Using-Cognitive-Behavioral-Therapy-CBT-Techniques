import { useState } from "react";
import ChatApp from "./ChatApp"; // jeśli używasz; w razie czego usuń tę linię
import BreathingTimer from "./components/BreathingTimer";

export default function App() {
  const [showBreathing, setShowBreathing] = useState(true);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold mb-4">AI CBT Chat</h1>

        <div className="flex gap-3 mb-6">
          <button
            onClick={() => setShowBreathing(true)}
            className={`px-4 py-2 rounded ${showBreathing ? "bg-indigo-600 text-white" : "bg-white border"}`}
          >
            Breathing
          </button>
          <button
            onClick={() => setShowBreathing(false)}
            className={`px-4 py-2 rounded ${!showBreathing ? "bg-indigo-600 text-white" : "bg-white border"}`}
          >
            Chat
          </button>
        </div>

        {showBreathing ? (
          <BreathingTimer
            pattern={[4, 7, 8]}              // 4-7-8 przykładowy
            cycles={4}
            phaseLabels={{ inhale: "Inhale", hold: "Hold", exhale: "Exhale" }}
            onDone={() => alert("Nice! Session complete.")}
          />
        ) : (
          <div className="bg-white rounded-2xl shadow p-6">
            {/* Tutaj wstaw <ChatApp /> jeśli już je masz */}
            <p>Chat screen goes here…</p>
          </div>
        )}
      </div>
    </div>
  );
}
