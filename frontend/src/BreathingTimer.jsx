import React, { useEffect, useState } from "react";

export default function BreathingTimer({
  inhale = 4,
  hold = 4,
  exhale = 6,
  cycles = 4,
  onDone,
  lang = "en",
}) {
  const labels = lang === "pl"
    ? { inhale: "Wdech", hold: "Wstrzymaj", exhale: "Wydech", done: "Koniec" }
    : { inhale: "Inhale", hold: "Hold", exhale: "Exhale", done: "Done" };

  const [phase, setPhase] = useState("inhale");
  const [timeLeft, setTimeLeft] = useState(inhale);
  const [cycle, setCycle] = useState(1);

  useEffect(() => {
    if (cycle > cycles) {
      onDone?.();
      return;
    }
    const tick = setInterval(() => {
      setTimeLeft((t) => {
        if (t > 1) return t - 1;

        // następna faza
        if (phase === "inhale") {
          setPhase("hold");
          return hold || 1;
        }
        if (phase === "hold") {
          setPhase("exhale");
          return exhale || 1;
        }
        if (phase === "exhale") {
          // kolejny cykl
          setPhase("inhale");
          setCycle((c) => c + 1);
          return inhale || 1;
        }
        return 1;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [phase, cycle, cycles, inhale, hold, exhale, onDone]);

  const phaseLabel =
    phase === "inhale" ? labels.inhale :
    phase === "hold"   ? labels.hold   :
    labels.exhale;

  return (
    <div style={{
      width: 280,
      marginTop: 12,
      padding: 16,
      borderRadius: 12,
      background: "white",
      boxShadow: "0 6px 24px rgba(0,0,0,.08)"
    }}>
      <div style={{ fontSize: 14, opacity: .7 }}>
        {lang === "pl" ? "Cykl" : "Cycle"} {Math.min(cycle, cycles)} / {cycles}
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, marginTop: 6 }}>{phaseLabel}</div>
      <div style={{ fontSize: 48, fontVariantNumeric: "tabular-nums", marginTop: 8 }}>
        {cycle > cycles ? labels.done : timeLeft}
      </div>
    </div>
  );
}
