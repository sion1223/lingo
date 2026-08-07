// Supabase Edge Function: lingo — stable entry point for the static web bundle.
// Deploy web/ to an always-on static host and inject its URL as LINGO_STATIC_APP_URL.

const STATIC_APP_URL = (Deno.env.get("LINGO_STATIC_APP_URL") ?? "").trim();

function unavailable() {
  return new Response("링고 정적 앱 주소가 설정되지 않았습니다.", {
    status: 503,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

Deno.serve((request) => {
  if (!STATIC_APP_URL) return unavailable();
  try {
    const destination = new URL(STATIC_APP_URL);
    destination.search = new URL(request.url).search;
    return Response.redirect(destination, 302);
  } catch {
    return unavailable();
  }
});
