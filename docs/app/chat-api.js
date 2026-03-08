import {
  CHARS_PER_TOKEN_ESTIMATE,
  MODEL_MAX_CONTEXT_TOKENS,
  TOKEN_SAFETY_MARGIN,
} from "./config.js";
import { getEffectiveBaseUrl } from "./settings.js";

export function createChatApi({ state, gcCount }) {
  function authHeaders(apiKey) {
    const headers = {};
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }
    return headers;
  }

  async function resolveModel(settings) {
    if (settings.modelName) return settings.modelName;

    const baseUrl = getEffectiveBaseUrl(settings.baseUrl);
    const cacheKey = JSON.stringify({
      baseUrl,
      apiKey: settings.apiKey,
    });

    if (state.modelCache[cacheKey]) {
      return state.modelCache[cacheKey];
    }

    const response = await fetch(`${baseUrl}/v1/models`, {
      method: "GET",
      headers: authHeaders(settings.apiKey),
    });

    if (!response.ok) {
      throw new Error(`Model discovery failed: ${response.status} ${response.statusText}`);
    }

    const payload = await response.json();
    const data = Array.isArray(payload.data) ? payload.data : [];
    if (!data.length || !data[0].id) {
      throw new Error("Remote server returned no models.");
    }

    const modelId = String(data[0].id).trim();
    state.modelCache[cacheKey] = modelId;
    gcCount("model/discovered", "Model discovered");
    return modelId;
  }

  function estimateTokens(text) {
    if (!text) return 0;
    return Math.ceil(String(text).length / CHARS_PER_TOKEN_ESTIMATE);
  }

  function messageTokenCost(message) {
    return estimateTokens(message.content) + 8;
  }

  function trimToEstimatedTokens(text, maxTokens) {
    if (maxTokens <= 0) return "";
    const maxChars = Math.max(1, Math.floor(maxTokens * CHARS_PER_TOKEN_ESTIMATE));
    if (text.length <= maxChars) return text;
    return text.slice(0, maxChars);
  }

  function buildMessages(settings, userText) {
    const systemPrompt = settings.systemPrompt.trim();
    const inputTokenBudget = Math.max(
      1,
      MODEL_MAX_CONTEXT_TOKENS - settings.maxNewTokens - TOKEN_SAFETY_MARGIN
    );

    const outgoing = [];
    let usedTokens = 0;

    if (systemPrompt) {
      const systemMessage = {
        role: "system",
        content: systemPrompt,
      };
      outgoing.push(systemMessage);
      usedTokens += messageTokenCost(systemMessage);
    }

    const reservedUserOverhead = 8;
    let historySlice = [];

    for (const sliceSize of [8, 6, 4, 2, 0]) {
      const candidate = state.history.slice(-sliceSize);
      const sliceCost = candidate.reduce((sum, message) => sum + messageTokenCost(message), 0);
      if (usedTokens + sliceCost + reservedUserOverhead <= inputTokenBudget) {
        historySlice = candidate;
        break;
      }
    }

    for (const message of historySlice) {
      outgoing.push(message);
      usedTokens += messageTokenCost(message);
    }

    const availableForUser = Math.max(1, inputTokenBudget - usedTokens - reservedUserOverhead);
    const trimmedUserText = trimToEstimatedTokens(userText, availableForUser);
    const userMessage = {
      role: "user",
      content: trimmedUserText,
    };

    outgoing.push(userMessage);

    return {
      messages: outgoing,
      userText: trimmedUserText,
      wasTokenTrimmed: trimmedUserText.length < userText.length,
    };
  }

  async function streamCompletion({ settings, userText, onToken }) {
    const controller = new AbortController();
    const timeoutMs = settings.requestTimeout * 1000;
    const timeoutId = window.setTimeout(() => controller.abort("timeout"), timeoutMs);

    try {
      const baseUrl = getEffectiveBaseUrl(settings.baseUrl);
      const model = await resolveModel(settings);
      const prepared = buildMessages(settings, userText);
      const response = await fetch(`${baseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(settings.apiKey),
        },
        body: JSON.stringify({
          model,
          messages: prepared.messages,
          temperature: settings.temperature,
          top_p: settings.topP,
          max_tokens: settings.maxNewTokens,
          stream: true,
        }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        let body = "";
        try {
          body = (await response.text()).slice(0, 600);
        } catch (_err) {}
        throw new Error(`Remote error ${response.status}: ${body || response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let output = "";
      let sawToken = false;
      let countedResponseStart = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;

          const data = line.slice(6).trim();
          if (!data) continue;

          if (data === "[DONE]") {
            return {
              output,
              userText: prepared.userText,
              wasTokenTrimmed: prepared.wasTokenTrimmed,
            };
          }

          let payload;
          try {
            payload = JSON.parse(data);
          } catch (_err) {
            continue;
          }

          const choice = (payload.choices || [])[0] || {};
          const delta = choice.delta || {};
          const token = delta.content;
          if (!token) continue;

          sawToken = true;
          if (!countedResponseStart) {
            countedResponseStart = true;
            gcCount("chat/response-start", "Response start");
          }

          output += token;
          if (typeof onToken === "function") {
            onToken(output);
          }
        }
      }

      if (!sawToken) {
        throw new Error("Remote server returned no streamed content.");
      }

      return {
        output,
        userText: prepared.userText,
        wasTokenTrimmed: prepared.wasTokenTrimmed,
      };
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  return {
    buildMessages,
    streamCompletion,
  };
}
