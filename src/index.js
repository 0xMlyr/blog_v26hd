const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return json({ error: "Method not allowed" }, 405, { allow: "GET" });
    }

    try {
      if (url.pathname === "/api/health") {
        return json({ ok: true });
      }

      if (url.pathname === "/api/articles") {
        return await listArticles(env);
      }

      if (url.pathname.startsWith("/api/article/")) {
        return await getArticle(env, url.pathname);
      }

      return json({ error: "Not found" }, 404);
    } catch (error) {
      console.error(error);
      return json({ error: "Internal server error" }, 500);
    }
  },
};

async function listArticles(env) {
  if (!env.BLOG_KV) {
    return json({ error: "KV binding BLOG_KV is not configured" }, 500);
  }

  const ids = [];
  let cursor;

  do {
    const page = await env.BLOG_KV.list({ cursor, limit: 1000 });
    for (const key of page.keys) {
      ids.push(key.name);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);

  ids.sort((a, b) => b.localeCompare(a, undefined, { numeric: true, sensitivity: "base" }));

  return json({ ids, count: ids.length });
}

async function getArticle(env, pathname) {
  if (!env.BLOG_KV) {
    return json({ error: "KV binding BLOG_KV is not configured" }, 500);
  }

  let id;
  try {
    id = decodeURIComponent(pathname.slice("/api/article/".length));
  } catch {
    return json({ error: "Invalid article id" }, 400);
  }

  if (!id) {
    return json({ error: "Missing article id" }, 400);
  }

  if (new TextEncoder().encode(id).length > 512) {
    return json({ error: "Article id is too long" }, 400);
  }

  const raw = await env.BLOG_KV.get(id);
  if (raw === null) {
    return json({ error: "Article not found" }, 404);
  }

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return json({ error: "Stored article is not valid JSON" }, 500);
  }

  if (!data || typeof data !== "object" || typeof data.article !== "string") {
    return json({ error: "Stored article must contain a string `article` field" }, 500);
  }

  return json({
    id,
    metadata: data.metadata && typeof data.metadata === "object" ? data.metadata : {},
    article: data.article,
  });
}

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...JSON_HEADERS, ...extraHeaders },
  });
}
