import { MODEL_MAX_CONTEXT_TOKENS, defaults } from "./config.js";
import { coercePositiveInt } from "./settings.js";

const ELLIPSIS = "\u2026";

export function createChatUi({
  elements,
  state,
  settings,
  analytics,
  power,
  welcome,
  chatApi,
}) {
  function updateInputCount() {
    const current = elements.input.value.length;
    const maxChars = coercePositiveInt(elements.maxInputChars.value, defaults.maxInputChars);
    elements.inputCount.textContent = `${current} / ${maxChars} chars`;
  }

  function scrollMessagesToBottom() {
    elements.messages.scrollTop = elements.messages.scrollHeight;
  }

  function createBubble(role, content) {
    welcome.clearWelcome();

    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = content;

    row.appendChild(bubble);
    elements.messages.appendChild(row);
    scrollMessagesToBottom();
    return bubble;
  }

  function clearChat() {
    if (state.busy) return;

    state.history = [];
    elements.messages.textContent = "";
    elements.messages.classList.remove("is-welcome");
    elements.input.value = "";
    elements.inputHint.textContent = "";
    updateInputCount();
    welcome.renderWelcome();
    elements.input.focus();
  }

  function sleep(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });
  }

  async function handleSend() {
    if (state.busy) return;

    const currentSettings = settings.getSettings();
    settings.saveSettings();

    const rawText = elements.input.value.trim();
    if (!rawText) return;

    const charClampedText = rawText.slice(0, currentSettings.maxInputChars);
    const wasCharTruncated = rawText.length > charClampedText.length;

    const prepared = chatApi.buildMessages(currentSettings, charClampedText);
    const userText = prepared.userText;
    const wasTokenTrimmed = prepared.wasTokenTrimmed;

    elements.input.value = "";
    updateInputCount();

    if (wasTokenTrimmed) {
      elements.inputHint.textContent = `Input trimmed to fit the ${MODEL_MAX_CONTEXT_TOKENS}-token context window.`;
      analytics.gcCount("chat/input-trimmed/context", "Input trimmed for context");
    } else if (wasCharTruncated) {
      elements.inputHint.textContent = `Input truncated to ${currentSettings.maxInputChars} chars before send.`;
      analytics.gcCount("chat/input-trimmed/chars", "Input truncated by character limit");
    } else {
      elements.inputHint.textContent = "Streaming response...";
    }

    analytics.gcCount("chat/send", "Chat send");

    createBubble("user", userText);
    const assistantBubble = createBubble("assistant", "");

    power.setBusy(true, "Streaming");

    const SILENT_DELAYS = [1000, 2000, 4000];
    const MAX_RETRIES = 9;
    let attempt = 0;
    let lastErr = null;

    while (true) {
      assistantBubble.textContent = "";
      try {
        const result = await chatApi.streamCompletion({
          settings: currentSettings,
          userText,
          onToken(output) {
            assistantBubble.textContent = output;
            scrollMessagesToBottom();
          },
        });

        state.history.push({ role: "user", content: result.userText });
        state.history.push({ role: "assistant", content: result.output });
        analytics.gcCount("chat/response-complete", "Response complete");
        analytics.countCompletedTurn();
        elements.inputHint.textContent = result.wasTokenTrimmed
          ? `Done. Input was trimmed to fit the ${MODEL_MAX_CONTEXT_TOKENS}-token context window.`
          : "Done.";
        power.setBusy(false, "Idle");
        break;
      } catch (err) {
        lastErr = err;
        attempt += 1;
        if (attempt > MAX_RETRIES) break;

        if (attempt <= 3) {
          await sleep(SILENT_DELAYS[attempt - 1]);
        } else {
          const backoffIndex = attempt - 4;
          const delay = Math.min(30000, 8000 * Math.pow(2, backoffIndex));
          assistantBubble.textContent = `Server busy, retrying${ELLIPSIS}`;
          elements.inputHint.textContent = `Server busy, retrying${ELLIPSIS}`;
          await sleep(delay);
        }
      }
    }

    if (lastErr && attempt > MAX_RETRIES) {
      const message = lastErr instanceof Error ? lastErr.message : "Request failed.";
      assistantBubble.textContent = `Error: ${message}`;
      elements.inputHint.textContent = message;
      analytics.gcCount("chat/response-error", "Response error");
      power.setBusy(false, "Error");
    }
  }

  return {
    updateInputCount,
    scrollMessagesToBottom,
    createBubble,
    clearChat,
    handleSend,
  };
}
