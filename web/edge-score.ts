// Supabase Edge Function: score — RunPod 채점 서버 프록시 + 제출 기록
import { createClient } from "jsr:@supabase/supabase-js@2";

function normalizeBaseUrl(value: string | undefined, envName: string) {
  const candidate = (value ?? "").trim();
  if (!candidate) return "";
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error(`${envName} must be an absolute HTTP(S) URL`);
  }
  if (
    !["http:", "https:"].includes(parsed.protocol)
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
  ) {
    throw new Error(`${envName} must be an HTTP(S) origin without credentials, query, or hash`);
  }
  return parsed.href.replace(/\/+$/, "");
}

const RUNPOD_BASE_URL = normalizeBaseUrl(
  Deno.env.get("RUNPOD_BASE_URL"),
  "RUNPOD_BASE_URL",
);
const CONFIGURED_TEACHER_BASE_URL = normalizeBaseUrl(
  Deno.env.get("TEACHER_BASE_URL"),
  "TEACHER_BASE_URL",
);
const TEACHER_BASE_URL = CONFIGURED_TEACHER_BASE_URL || RUNPOD_BASE_URL;
const TEACHER_API_TOKEN = (Deno.env.get("TEACHER_API_TOKEN") ?? "").trim();
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

function json(o: unknown, status = 200) {
  return new Response(JSON.stringify(o), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

const OFFLINE = {
  offline: true,
  message: "채점 서버(RunPod pod)가 꺼져 있습니다. pod를 켠 뒤 1~2분 후 다시 시도하세요.",
};
const TEACHER_OFFLINE = {
  offline: true,
  message: "선생님 설명 서버를 사용할 수 없습니다. 기본 설명을 표시합니다.",
};

const TEACHER_TIMEOUT_MS = 25_000;
const TEACHER_PROFILE_KEYS = new Set([
  "primary_direction",
  "relative_length",
  "start_height",
  "end_height",
  "start_position",
  "end_position",
  "path_shape",
  "stroke_order",
  "stroke_count",
]);

type RequestBody = {
  action?: string;
  char?: unknown;
  strokes?: unknown;
  [key: string]: unknown;
};

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function stringMap(value: unknown) {
  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(record(value))) {
    if (TEACHER_PROFILE_KEYS.has(key) && typeof item === "string") {
      result[key] = item.slice(0, 64);
    }
  }
  return result;
}

function stableCodes(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => (
      typeof item === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(item)
    )).slice(0, 32)
    : [];
}

function teacherProxyHeaders(): Record<string, string> {
  const headers = { "Content-Type": "application/json" };
  if (TEACHER_API_TOKEN) {
    return { ...headers, "X-Lingo-Teacher-Token": TEACHER_API_TOKEN };
  }
  return headers;
}

// Rebuild the body from the public teacher contract. This keeps raw strokes,
// images, pressure series, browser identities, and unrelated Edge fields out.
function teacherPayload(body: RequestBody) {
  const learner = record(body.learner);
  const task = record(body.task);
  const locked = record(body.locked_decision);
  const evidence = record(body.evidence);
  const policy = record(body.teaching_policy);
  return {
    schema_version: body.schema_version,
    locale: body.locale,
    learner: {
      level: learner.level,
      attempt_number: learner.attempt_number,
      same_error_count: learner.same_error_count,
      preferred_length: learner.preferred_length,
    },
    task: {
      target_char: task.target_char,
      nearest_competitor: task.nearest_competitor,
      mode: task.mode,
      critical_stroke: task.critical_stroke,
      total_strokes: task.total_strokes,
    },
    locked_decision: {
      decision_id: locked.decision_id,
      error_code: locked.error_code,
      evidence_codes: stableCodes(locked.evidence_codes),
      severity: locked.severity,
      confidence: locked.confidence,
      accepted: locked.accepted,
      next_action: locked.next_action,
    },
    evidence: {
      target_margin: evidence.target_margin,
      critical_region: evidence.critical_region,
      target_feature_profile: stringMap(evidence.target_feature_profile),
      observed_feature_profile: stringMap(evidence.observed_feature_profile),
    },
    teaching_policy: {
      allowed_strategies: Array.isArray(policy.allowed_strategies)
        ? policy.allowed_strategies.filter((item) => typeof item === "string")
        : [],
      max_sentences: policy.max_sentences,
      max_characters: policy.max_characters,
      must_preserve_locked_fields: policy.must_preserve_locked_fields,
      forbidden: Array.isArray(policy.forbidden)
        ? policy.forbidden.filter((item) => typeof item === "string")
        : [],
    },
  };
}

function upstreamUrl(baseUrl: string, path: string) {
  if (!baseUrl) throw new Error("upstream base URL is not configured");
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("upstream path must be an origin-relative path");
  }
  return `${baseUrl}${path}`;
}

async function upstreamFetch(
  baseUrl: string,
  path: string,
  init: RequestInit,
  timeoutMs: number,
  nonJsonBody: JsonRecord = OFFLINE,
) {
  const r = await fetch(upstreamUrl(baseUrl, path), {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await r.text();
  try {
    return { status: r.status, body: JSON.parse(text) };
  } catch {
    // RunPod proxy와 일부 CPU hosts는 장애 시 HTML을 반환할 수 있다.
    return { status: 503, body: nonJsonBody };
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  let body: RequestBody;
  try {
    body = await req.json() as RequestBody;
  } catch {
    return json({ code: "INVALID_REQUEST", message: "JSON 요청 본문이 필요합니다." }, 400);
  }
  try {
    if (body.action === "health") {
      try {
        const { status, body: b } = await upstreamFetch(
          RUNPOD_BASE_URL, "/health", {}, 8000);
        return json(b, status);
      } catch {
        return json({ ok: false, ...OFFLINE }, 503);
      }
    }
    if (body.action === "template") {
      try {
        const { status, body: b } = await upstreamFetch(
          RUNPOD_BASE_URL,
          `/template/${encodeURIComponent(String(body.char ?? ""))}`,
          {},
          20000,
        );
        return json(b, status);
      } catch {
        return json(OFFLINE, 503);
      }
    }
    if (body.action === "coach") {
      const coachPayload = { ...body };
      delete coachPayload.action;
      try {
        const { status, body: coachBody } = await upstreamFetch(
          RUNPOD_BASE_URL,
          "/coach/stroke",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(coachPayload),
          },
          2500,
        );
        return json(coachBody, status);
      } catch (error) {
        const timedOut = error instanceof DOMException && error.name === "TimeoutError";
        return json(
          { message: timedOut ? "실시간 교정 응답 시간이 초과됐습니다." : OFFLINE.message },
          timedOut ? 504 : 503,
        );
      }
    }
    if (body.action === "verbalize" || body.action === "summary") {
      try {
        const { status, body: teacherBody } = await upstreamFetch(
          TEACHER_BASE_URL,
          body.action === "summary" ? "/coach/summary" : "/coach/verbalize",
          {
            method: "POST",
            headers: teacherProxyHeaders(),
            body: JSON.stringify(teacherPayload(body)),
          },
          TEACHER_TIMEOUT_MS,
          TEACHER_OFFLINE,
        );
        return json(teacherBody, status);
      } catch (error) {
        const timedOut = error instanceof DOMException && error.name === "TimeoutError";
        return json(
          {
            code: timedOut ? "TEACHER_TIMEOUT" : "TEACHER_UNAVAILABLE",
            message: timedOut
              ? "선생님 설명 응답 시간이 초과됐습니다. 기본 설명을 표시합니다."
              : "선생님 설명 서버를 사용할 수 없습니다. 기본 설명을 표시합니다.",
          },
          timedOut ? 504 : 503,
        );
      }
    }
    if (body.action && body.action !== "score") {
      return json({ code: "UNSUPPORTED_ACTION", message: "지원하지 않는 요청입니다." }, 400);
    }
    // 채점
    let res;
    try {
      res = await upstreamFetch(
        RUNPOD_BASE_URL,
        "/score",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ char: body.char, strokes: body.strokes }),
        },
        150000,
      );
    } catch {
      return json(OFFLINE, 503);
    }
    if (res.status === 200) {
      const testRunId = typeof body.test_run_id === "string"
        ? body.test_run_id.slice(0, 128)
        : null;
      const storedReport = testRunId
        ? { ...res.body, test_run_id: testRunId }
        : res.body;
      const sb = createClient(
        Deno.env.get("SUPABASE_URL")!,
        Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
      );
      const { error } = await sb.from("submissions").insert({
        chr: body.char,
        strokes: body.strokes,
        score: res.body.score,
        report: storedReport,
      });
      if (error) console.error("submissions insert:", error.message);
    }
    return json(res.body, res.status);
  } catch {
    return json({ error: "요청을 처리하지 못했습니다." }, 500);
  }
});
