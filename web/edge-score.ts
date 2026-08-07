// Supabase Edge Function: score — RunPod 채점 서버 프록시 + 제출 기록
import { createClient } from "jsr:@supabase/supabase-js@2";

const POD = (Deno.env.get("RUNPOD_BASE_URL") ?? "").replace(/\/+$/, "");
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

type RequestBody = {
  action?: string;
  char?: unknown;
  strokes?: unknown;
  [key: string]: unknown;
};

async function podFetch(path: string, init: RequestInit, timeoutMs: number) {
  if (!POD) throw new Error("RUNPOD_BASE_URL is not configured");
  const r = await fetch(`${POD}${path}`, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await r.text();
  try {
    return { status: r.status, body: JSON.parse(text) };
  } catch {
    // pod가 꺼져 있으면 runpod proxy가 HTML 에러 페이지를 반환한다
    return { status: 503, body: OFFLINE };
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
        const { status, body: b } = await podFetch("/health", {}, 8000);
        return json(b, status);
      } catch {
        return json({ ok: false, ...OFFLINE }, 503);
      }
    }
    if (body.action === "template") {
      try {
        const { status, body: b } = await podFetch(
          `/template/${encodeURIComponent(String(body.char ?? ""))}`, {}, 20000);
        return json(b, status);
      } catch {
        return json(OFFLINE, 503);
      }
    }
    if (body.action === "coach") {
      const coachPayload = { ...body };
      delete coachPayload.action;
      try {
        const { status, body: coachBody } = await podFetch(
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
    // 채점
    let res;
    try {
      res = await podFetch("/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ char: body.char, strokes: body.strokes }),
      }, 150000);
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
