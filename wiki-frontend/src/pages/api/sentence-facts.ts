import type { APIRoute } from "astro";
import { getSentenceFacts } from "../../lib/api.js";

export const GET: APIRoute = async ({ url }) => {
  const synthesisId = url.searchParams.get("synthesisId");
  const position = parseInt(url.searchParams.get("position") ?? "0", 10);

  if (!synthesisId) {
    return new Response(JSON.stringify({ error: "synthesisId is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const facts = await getSentenceFacts(synthesisId, position);
    return new Response(JSON.stringify(facts), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return new Response(JSON.stringify({ error: message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
