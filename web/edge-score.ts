// Supabase Edge Function: score — RunPod 채점 서버 프록시 + 제출 기록
import { createClient } from "jsr:@supabase/supabase-js@2";

const POD = "https://l8faq6mx5shxpc-8000.proxy.runpod.net";
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

async function podFetch(path: string, init: RequestInit, timeoutMs: number) {
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
  try {
    const body = await req.json();
    if (body.action === "health") {
      try {
        const { status, body: b } = await podFetch("/health", {}, 8000);
        return json(b, status);
      } catch {
        return json({ ok: false, ...OFFLINE });
      }
    }
    if (body.action === "template") {
      try {
        const { status, body: b } = await podFetch(
          `/template/${encodeURIComponent(body.char)}`, {}, 20000);
        return json(b, status);
      } catch {
        return json(OFFLINE, 503);
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
      const sb = createClient(
        Deno.env.get("SUPABASE_URL")!,
        Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
      );
      const { error } = await sb.from("submissions").insert({
        chr: body.char,
        strokes: body.strokes,
        score: res.body.score,
        report: res.body,
      });
      if (error) console.error("submissions insert:", error.message);
    }
    return json(res.body, res.status);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
