function metaContent(name) {
  return globalThis.document?.querySelector(`meta[name="${name}"]`)?.content?.trim() || "";
}

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

export function resolveRuntimeConfig(overrides = {}) {
  const runtime = globalThis.LINGO_CONFIG || {};
  return {
    apiBaseUrl: trimTrailingSlash(
      overrides.apiBaseUrl
      ?? runtime.apiBaseUrl
      ?? metaContent("lingo-api-base-url")
      ?? "",
    ),
    edgeEndpoint: overrides.edgeEndpoint
      ?? runtime.edgeEndpoint
      ?? metaContent("lingo-edge-endpoint")
      ?? "",
    apiKey: overrides.apiKey
      ?? runtime.apiKey
      ?? metaContent("lingo-api-key")
      ?? "",
  };
}

function joinUrl(base, path) {
  if (/^https?:\/\//i.test(path)) return path;
  if (!base) return path;
  return `${trimTrailingSlash(base)}/${path.replace(/^\/+/, "")}`;
}

async function parseResponse(response) {
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { message: "서버가 JSON이 아닌 응답을 반환했습니다." };
  }
  return { status: response.status, body };
}

export class ApiClient {
  constructor(config = {}) {
    this.config = resolveRuntimeConfig(config);
  }

  async request(action, payload = {}, { signal, keepalive = false } = {}) {
    if (this.config.edgeEndpoint && action !== "chars") {
      const headers = { "Content-Type": "application/json" };
      if (this.config.apiKey) {
        headers.Authorization = `Bearer ${this.config.apiKey}`;
        headers.apikey = this.config.apiKey;
      }
      const response = await fetch(this.config.edgeEndpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({ ...payload, action }),
        signal,
        keepalive,
      });
      return parseResponse(response);
    }

    const base = this.config.apiBaseUrl;
    let url;
    let init = { signal };
    if (action === "health") {
      url = joinUrl(base, "/health");
    } else if (action === "template") {
      url = joinUrl(base, `/template/${encodeURIComponent(payload.char)}`);
    } else if (action === "chars") {
      url = joinUrl(base, "/chars");
    } else if (action === "score") {
      url = joinUrl(base, "/score");
      init = {
        ...init,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          char: payload.char,
          strokes: payload.strokes,
          mode: payload.mode,
        }),
      };
    } else if (action === "coach") {
      url = joinUrl(base, "/coach/stroke");
      init = {
        ...init,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
    } else if (action === "attempt") {
      url = joinUrl(base, "/attempt/events");
      init = {
        ...init,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive,
      };
    } else if (action === "verbalize" || action === "summary") {
      url = joinUrl(base, action === "summary" ? "/coach/summary" : "/coach/verbalize");
      init = {
        ...init,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
    } else {
      throw new RangeError(`unsupported API action: ${action}`);
    }
    return parseResponse(await fetch(url, init));
  }
}
