import assert from "node:assert/strict";
import test from "node:test";

import { ApiClient } from "../api.js";

test("direct API mode posts the complete coach contract to /coach/stroke", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, init) => {
    captured = { url, init };
    return new Response('{"ok":true}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ApiClient({ apiBaseUrl: "https://api.example.test/" });
    const payload = { protocol_version: 1, request_id: "request-1" };
    const response = await client.request("coach", payload);

    assert.equal(captured.url, "https://api.example.test/coach/stroke");
    assert.equal(captured.init.method, "POST");
    assert.deepEqual(JSON.parse(captured.init.body), payload);
    assert.equal(response.status, 200);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
