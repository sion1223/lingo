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

test("direct API mode routes teacher verbalize and summary separately", async () => {
  const originalFetch = globalThis.fetch;
  const captured = [];
  globalThis.fetch = async (url, init) => {
    captured.push({ url, init });
    return new Response('{"source":"fallback"}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ApiClient({ apiBaseUrl: "https://api.example.test/" });
    const payload = { schema_version: "teacher_feedback.v1" };
    await client.request("verbalize", payload);
    await client.request("summary", payload);

    assert.equal(captured[0].url, "https://api.example.test/coach/verbalize");
    assert.equal(captured[1].url, "https://api.example.test/coach/summary");
    for (const request of captured) {
      assert.equal(request.init.method, "POST");
      assert.deepEqual(JSON.parse(request.init.body), payload);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("direct API mode posts a complete attempt batch without flattening rich points", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, init) => {
    captured = { url, init };
    return new Response('{"stored":true}', {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ApiClient({ apiBaseUrl: "https://api.example.test/" });
    const payload = {
      protocol_version: 1,
      char: "語",
      strokes: [[{ x: 0.2, y: 0.3, t: 10, pressure: 0.5 }]],
    };
    const response = await client.request("attempt", payload);

    assert.equal(captured.url, "https://api.example.test/attempt/events");
    assert.deepEqual(JSON.parse(captured.init.body), payload);
    assert.equal(response.status, 202);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Edge API mode pins the selected action after the structured payload", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, init) => {
    captured = { url, init };
    return new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ApiClient({ edgeEndpoint: "https://edge.example.test/score" });
    await client.request("verbalize", {
      action: "score",
      schema_version: "teacher_feedback.v1",
    });
    assert.equal(captured.url, "https://edge.example.test/score");
    assert.deepEqual(JSON.parse(captured.init.body), {
      action: "verbalize",
      schema_version: "teacher_feedback.v1",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
