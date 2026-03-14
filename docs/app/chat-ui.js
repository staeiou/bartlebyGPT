import { MODEL_MAX_CONTEXT_TOKENS, defaults, FEEDBACK_PATH } from "./config.js?v=20260314e9";
import { coercePositiveInt, getEffectiveBaseUrl } from "./settings.js?v=20260314e9";

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

  function sendFeedback(rating, messages) {
    // API call disabled until server endpoint is ready.
    // To re-enable: remove the return below and ensure FEEDBACK_PATH
    // ("/v1/feedback") is implemented on the server.
    return;
    const baseUrl = getEffectiveBaseUrl(settings.getSettings().baseUrl); // eslint-disable-line no-unreachable
    fetch(baseUrl + FEEDBACK_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, messages }),
    }).catch(() => {});
  }

  function updateLastRowClasses() {
    let lastAsst = null;
    let lastUser = null;
    for (const entry of state.messages) {
      if (entry.role === "assistant") lastAsst = entry;
      if (entry.role === "user") lastUser = entry;
    }
    for (const entry of state.messages) {
      entry.rowEl.classList.remove("is-last-assistant", "is-last-user");
    }
    if (lastAsst) lastAsst.rowEl.classList.add("is-last-assistant");
    if (lastUser) lastUser.rowEl.classList.add("is-last-user");
  }

  function setMessageContent(entry, content) {
    entry.content = content;
    entry.bubbleEl.textContent = content;
  }

  function appendHistoryTurn(userContent, assistantContent) {
    state.history.push({ role: "user", content: userContent });
    state.history.push({ role: "assistant", content: assistantContent });
  }

  function setTurnHistoryIncluded(userEntry, assistantEntry, included) {
    userEntry.historyIncluded = included;
    assistantEntry.historyIncluded = included;
  }

  function removeHistoryTurnIfIncluded(userEntry, assistantEntry) {
    if (!userEntry.historyIncluded || !assistantEntry.historyIncluded) {
      return false;
    }

    if (state.history.length >= 2) {
      state.history.pop();
      state.history.pop();
    }

    setTurnHistoryIncluded(userEntry, assistantEntry, false);
    return true;
  }

  function getLastTurnEntries() {
    if (state.messages.length < 2) return null;

    const assistantEntry = state.messages[state.messages.length - 1];
    const userEntry = state.messages[state.messages.length - 2];
    if (assistantEntry.role !== "assistant" || userEntry.role !== "user") {
      return null;
    }

    return { userEntry, assistantEntry };
  }

  function createBubble(role, content) {
    welcome.clearWelcome();

    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = content;

    const actions = document.createElement("div");
    actions.className = "message-actions";

    if (role === "assistant") {
      const thumbUpBtn = document.createElement("button");
      thumbUpBtn.className = "action-btn feedback-btn";
      thumbUpBtn.title = "Good response";
      thumbUpBtn.textContent = "\uD83D\uDC4D";

      const thumbDownBtn = document.createElement("button");
      thumbDownBtn.className = "action-btn feedback-btn";
      thumbDownBtn.title = "Bad response";
      thumbDownBtn.textContent = "\uD83D\uDC4E";

      [thumbUpBtn, thumbDownBtn].forEach((btn, i) => {
        const rating = i === 0 ? "up" : "down";
        btn.addEventListener("click", () => {
          thumbUpBtn.disabled = true;
          thumbDownBtn.disabled = true;
          btn.classList.add("is-selected");
          sendFeedback(rating, state.history.slice());
        });
      });

      const refreshBtn = document.createElement("button");
      refreshBtn.className = "action-btn refresh-btn";
      refreshBtn.title = "Regenerate response";
      refreshBtn.textContent = "\u21ba";
      refreshBtn.addEventListener("click", () => void regenerateLast());

      actions.appendChild(thumbUpBtn);
      actions.appendChild(thumbDownBtn);
      actions.appendChild(refreshBtn);
      row.appendChild(bubble);
      row.appendChild(actions);
    } else {
      const editBtn = document.createElement("button");
      editBtn.className = "action-btn edit-btn";
      editBtn.title = "Edit message";
      editBtn.textContent = "\u270e";
      editBtn.addEventListener("click", () => editLast());
      actions.appendChild(editBtn);
      row.appendChild(actions);
      row.appendChild(bubble);
    }

    elements.messages.appendChild(row);
    scrollMessagesToBottom();
    const entry = {
      role,
      rowEl: row,
      bubbleEl: bubble,
      content,
      historyIncluded: false,
    };
    state.messages.push(entry);
    updateLastRowClasses();
    return entry;
  }

  function clearChat() {
    if (state.busy) return;

    state.history = [];
    state.messages = [];
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

  async function runStreamWithRetry({ assistantEntry, userText, currentSettings }) {
    const SILENT_DELAYS = [1000, 2000, 4000];
    const MAX_RETRIES = 9;
    let attempt = 0;
    let lastErr = null;

    while (true) {
      setMessageContent(assistantEntry, "");
      try {
        const result = await chatApi.streamCompletion({
          settings: currentSettings,
          userText,
          onToken(output) {
            setMessageContent(assistantEntry, output);
            scrollMessagesToBottom();
          },
        });
        return { result };
      } catch (err) {
        lastErr = err;
        if (err instanceof Error && err.retryable === false) {
          break;
        }
        attempt += 1;
        if (attempt > MAX_RETRIES) break;

        if (attempt <= 3) {
          await sleep(SILENT_DELAYS[attempt - 1]);
        } else {
          const backoffIndex = attempt - 4;
          const delay = Math.min(30000, 8000 * Math.pow(2, backoffIndex));
          setMessageContent(assistantEntry, `Server busy, retrying${ELLIPSIS}`);
          elements.inputHint.textContent = `Server busy, retrying${ELLIPSIS}`;
          await sleep(delay);
        }
      }
    }

    return { error: lastErr };
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

    const userEntry = createBubble("user", userText);
    const assistantEntry = createBubble("assistant", "");

    power.setBusy(true, "Streaming");

    const { result, error } = await runStreamWithRetry({
      assistantEntry,
      userText,
      currentSettings,
    });

    if (error) {
      const message = error instanceof Error ? error.message : "Request failed.";
      setMessageContent(assistantEntry, `Error: ${message}`);
      elements.inputHint.textContent = message;
      analytics.gcCount("chat/response-error", "Response error");
      power.setBusy(false, "Error");
    } else {
      appendHistoryTurn(result.userText, result.output);
      setTurnHistoryIncluded(userEntry, assistantEntry, true);
      setMessageContent(assistantEntry, result.output);
      analytics.gcCount("chat/response-complete", "Response complete");
      analytics.countCompletedTurn();
      elements.inputHint.textContent = result.wasTokenTrimmed
        ? `Done. Input was trimmed to fit the ${MODEL_MAX_CONTEXT_TOKENS}-token context window.`
        : "Done.";
      power.setBusy(false, "Idle");
    }
  }

  async function regenerateLast() {
    if (state.busy) return;
    const lastTurn = getLastTurnEntries();
    if (!lastTurn) return;

    const { userEntry: lastUser, assistantEntry: lastAsst } = lastTurn;
    const userText = lastUser.content;

    removeHistoryTurnIfIncluded(lastUser, lastAsst);

    const currentSettings = settings.getSettings();
    elements.inputHint.textContent = `Regenerating${ELLIPSIS}`;
    power.setBusy(true, "Streaming");
    analytics.gcCount("chat/regenerate", "Regenerate");

    const { result, error } = await runStreamWithRetry({
      assistantEntry: lastAsst,
      userText,
      currentSettings,
    });

    // Reset feedback buttons on the reused assistant row
    lastAsst.rowEl.querySelectorAll(".feedback-btn").forEach((btn) => {
      btn.disabled = false;
      btn.classList.remove("is-selected");
    });

    if (error) {
      const message = error instanceof Error ? error.message : "Request failed.";
      setMessageContent(lastAsst, `Error: ${message}`);
      elements.inputHint.textContent = message;
      analytics.gcCount("chat/response-error", "Response error");
      power.setBusy(false, "Error");
    } else {
      appendHistoryTurn(result.userText, result.output);
      setTurnHistoryIncluded(lastUser, lastAsst, true);
      setMessageContent(lastUser, result.userText);
      setMessageContent(lastAsst, result.output);
      analytics.countCompletedTurn();
      elements.inputHint.textContent = result.wasTokenTrimmed
        ? `Done. Input was trimmed to fit the ${MODEL_MAX_CONTEXT_TOKENS}-token context window.`
        : "Done.";
      power.setBusy(false, "Idle");
    }

    updateLastRowClasses();
  }

  function editLast() {
    if (state.busy) return;
    const lastTurn = getLastTurnEntries();
    if (!lastTurn) return;

    const { userEntry: lastUser, assistantEntry: lastAsst } = lastTurn;
    const hadHistory = removeHistoryTurnIfIncluded(lastUser, lastAsst);

    state.messages.pop();
    state.messages.pop();

    updateLastRowClasses();

    // Remove assistant row from DOM; keep user row but enter edit mode
    lastAsst.rowEl.remove();

    const originalText = lastUser.bubbleEl.textContent;
    const bubbleHeight = lastUser.bubbleEl.offsetHeight;
    lastUser.bubbleEl.textContent = "";

    const textarea = document.createElement("textarea");
    textarea.className = "edit-textarea";
    textarea.style.height = bubbleHeight + "px";
    textarea.value = originalText;
    lastUser.bubbleEl.appendChild(textarea);

    const controls = document.createElement("div");
    controls.className = "edit-controls";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "edit-cancel-btn";
    cancelBtn.textContent = "Cancel";

    const saveBtn = document.createElement("button");
    saveBtn.className = "edit-save-btn";
    saveBtn.textContent = "Send";

    controls.appendChild(cancelBtn);
    controls.appendChild(saveBtn);
    lastUser.rowEl.appendChild(controls);
    lastUser.rowEl.classList.add("is-editing");

    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    function exitEditMode() {
      lastUser.rowEl.classList.remove("is-editing");
      controls.remove();
    }

    saveBtn.addEventListener("click", () => {
      const newText = textarea.value.trim();
      if (!newText) return;
      lastUser.rowEl.remove();
      exitEditMode();
      elements.input.value = newText;
      void handleSend();
    });

    cancelBtn.addEventListener("click", () => {
      setMessageContent(lastUser, originalText);
      exitEditMode();
      elements.messages.appendChild(lastAsst.rowEl);
      if (hadHistory) {
        appendHistoryTurn(originalText, lastAsst.content);
        setTurnHistoryIncluded(lastUser, lastAsst, true);
      }
      state.messages.push(lastUser);
      state.messages.push(lastAsst);
      updateLastRowClasses();
    });

    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        saveBtn.click();
      }
      if (e.key === "Escape") {
        cancelBtn.click();
      }
    });
  }

  return {
    updateInputCount,
    scrollMessagesToBottom,
    createBubble,
    clearChat,
    handleSend,
    regenerateLast,
    editLast,
  };
}
