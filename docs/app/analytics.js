import { TURN_COUNT_KEY } from "./config.js?v=20260409a1";

export function createAnalytics(state) {
  function gcReady() {
    return Boolean(
      window.goatcounter &&
      typeof window.goatcounter.count === "function"
    );
  }

  function gcCount(path, title) {
    if (!gcReady()) return;
    const cleanPath = String(path || "").replace(/^\/+/, "");
    try {
      window.goatcounter.count({
        path: cleanPath,
        title: title || cleanPath,
        event: true,
      });
    } catch (_err) {}
  }

  function gcPageview() {
    if (!gcReady()) return;
    try {
      window.goatcounter.count({
        event: false,
      });
    } catch (_err) {}
  }

  function goatStart() {
    if (state.goatStartSent) return;

    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (!gcReady()) {
        if (Date.now() - startedAt > 15000) {
          window.clearInterval(timer);
        }
        return;
      }

      state.goatStartSent = true;
      window.clearInterval(timer);
      gcPageview();
      gcCount("app/start", "App start");
    }, 150);
  }

  function loadTurnCount() {
    try {
      const raw = sessionStorage.getItem(TURN_COUNT_KEY);
      const parsed = Number.parseInt(raw || "0", 10);
      return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    } catch (_err) {
      return 0;
    }
  }

  function saveTurnCount() {
    try {
      sessionStorage.setItem(TURN_COUNT_KEY, String(state.turnCount));
    } catch (_err) {}
  }

  function turnBucket(turnCount) {
    if (turnCount <= 10) return String(turnCount);
    if (turnCount <= 20) return "11-20";
    if (turnCount <= 50) return "21-50";
    return "51+";
  }

  function countCompletedTurn() {
    state.turnCount += 1;
    saveTurnCount();
    gcCount("session/turn/completed", "Completed turn");
    gcCount(
      `session/turn/${turnBucket(state.turnCount)}`,
      `Turn ${turnBucket(state.turnCount)}`
    );
  }

  return {
    gcCount,
    goatStart,
    loadTurnCount,
    countCompletedTurn,
  };
}
