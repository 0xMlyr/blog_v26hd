const page = document.body.dataset.page;

if (page === "list") {
  initList();
}

if (page === "article") {
  initArticle();
}

async function initList() {
  const status = document.getElementById("status");
  const list = document.getElementById("article-list");

  try {
    const response = await fetch("/api/articles", {
      headers: { accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const data = await response.json();
    const ids = Array.isArray(data.ids) ? data.ids : [];

    status.textContent = ids.length ? `共 ${ids.length} 篇文章` : "还没有文章";
    if (!ids.length) {
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const id of ids) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.className = "article-link";
      link.href = `/article.html?id=${encodeURIComponent(id)}`;
      link.textContent = id;
      item.append(link);
      fragment.append(item);
    }

    list.append(fragment);
    list.hidden = false;
  } catch (error) {
    status.textContent = `加载失败：${error.message}`;
    status.classList.add("error");
  }
}

async function initArticle() {
  const id = new URLSearchParams(location.search).get("id");
  const titleElement = document.getElementById("article-title");
  const metaElement = document.getElementById("article-meta");
  const contentElement = document.getElementById("article-content");

  if (!id) {
    contentElement.innerHTML = '<p class="status error">缺少文章编号。<a href="/">返回列表</a></p>';
    return;
  }

  try {
    const response = await fetch(`/api/article/${encodeURIComponent(id)}`, {
      headers: { accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const data = await response.json();
    const metadata = data.metadata || {};
    const title = typeof metadata.title === "string" && metadata.title.trim()
      ? metadata.title.trim()
      : id;

    document.title = `${title} · 文章`;

    const markdownStartsWithHeading = /^\s*#\s+/.test(data.article);
    if (!markdownStartsWithHeading) {
      titleElement.textContent = title;
      titleElement.hidden = false;
    }

    const metaItems = renderMetadata(metadata);
    if (metaItems.length) {
      metaElement.innerHTML = metaItems.join("");
      metaElement.hidden = false;
    }

    if (!window.marked || typeof window.marked.parse !== "function") {
      throw new Error("Markdown 渲染器未加载");
    }

    contentElement.innerHTML = window.marked.parse(data.article, { gfm: true });
  } catch (error) {
    contentElement.innerHTML = `<p class="status error">加载失败：${escapeHtml(error.message)}</p>`;
  }
}

function renderMetadata(metadata) {
  const entries = Object.entries(metadata).filter(([key, value]) => {
    if (key === "title" || value === null || value === undefined || value === "") {
      return false;
    }
    return !Array.isArray(value) || value.length > 0;
  });

  entries.sort(([left], [right]) => {
    if (left === "date") return -1;
    if (right === "date") return 1;
    return 0;
  });

  return entries.map(([key, value]) => {
    const label = `<span class="meta-key">${escapeHtml(key)}</span>`;

    if (Array.isArray(value)) {
      const chips = value
        .map((item) => `<span class="chip">${escapeHtml(String(item))}</span>`)
        .join("");
      return `<span class="meta-item">${label} ${chips}</span>`;
    }

    if (typeof value === "object") {
      return `<span class="meta-item">${label} ${escapeHtml(JSON.stringify(value))}</span>`;
    }

    return `<span class="meta-item">${label} ${escapeHtml(String(value))}</span>`;
  });
}

async function readError(response) {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string") {
      return data.error;
    }
  } catch {
    // Fall through to the HTTP status text.
  }
  return `HTTP ${response.status}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    switch (character) {
      case "&": return "&amp;";
      case "<": return "&lt;";
      case ">": return "&gt;";
      case '"': return "&quot;";
      case "'": return "&#39;";
      default: return character;
    }
  });
}
