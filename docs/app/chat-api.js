import {
  CHARS_PER_TOKEN_ESTIMATE,
  MODEL_MAX_CONTEXT_TOKENS,
  TOKEN_SAFETY_MARGIN,
} from "./config.js?v=20260314e8";
import { getEffectiveBaseUrl } from "./settings.js?v=20260314e8";

export function createChatApi({ state, gcCount }) {
  function createRequestError(message, { status, retryable = false, cause } = {}) {
    const error = new Error(message);
    if (Number.isFinite(status)) {
      error.status = status;
    }
    error.retryable = retryable;
    if (cause) {
      error.cause = cause;
    }
    return error;
  }

  function isRetryableStatus(status) {
    return status === 408 || status === 429 || status >= 500;
  }

  function normalizeRequestError(error, timeoutSeconds) {
    if (error instanceof Error && typeof error.retryable === "boolean") {
      return error;
    }

    if (error instanceof DOMException && error.name === "AbortError") {
      return createRequestError(`Request timed out after ${timeoutSeconds}s.`, {
        retryable: true,
        cause: error,
      });
    }

    if (error instanceof TypeError) {
      return createRequestError("Network error while contacting the server.", {
        retryable: true,
        cause: error,
      });
    }

    if (error instanceof Error) {
      return createRequestError(error.message, {
        retryable: false,
        cause: error,
      });
    }

    return createRequestError("Request failed.", { retryable: false });
  }

  function authHeaders(apiKey) {
    const headers = {};
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }
    return headers;
  }

  async function resolveModel(settings, signal) {
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
      signal,
    });

    if (!response.ok) {
      throw createRequestError(
        `Model discovery failed: ${response.status} ${response.statusText}`,
        {
          status: response.status,
          retryable: isRetryableStatus(response.status),
        }
      );
    }

    const payload = await response.json();
    const data = Array.isArray(payload.data) ? payload.data : [];
    if (!data.length || !data[0].id) {
      throw createRequestError("Remote server returned no models.", {
        retryable: false,
      });
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
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

    try {
      const baseUrl = getEffectiveBaseUrl(settings.baseUrl);
      const model = await resolveModel(settings, controller.signal);
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
        const message = response.ok
          ? "Remote server did not provide a streaming response."
          : `Remote error ${response.status}: ${body || response.statusText}`;
        throw createRequestError(message, {
          status: response.status,
          retryable: response.ok ? false : isRetryableStatus(response.status),
        });
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
        throw createRequestError("Remote server returned no streamed content.", {
          retryable: false,
        });
      }

      return {
        output,
        userText: prepared.userText,
        wasTokenTrimmed: prepared.wasTokenTrimmed,
      };
    } catch (error) {
      throw normalizeRequestError(error, settings.requestTimeout);
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  return {
    buildMessages,
    streamCompletion,
  };
}
