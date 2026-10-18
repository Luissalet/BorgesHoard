// Thin fetch wrapper. Every error surfaces as an Error with the server message.
async function request(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) throw Object.assign(new Error(data?.error || `Error ${response.status}`), { status: response.status });
  return data;
}

const qs = (params) => {
  const clean = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return clean.length ? `?${new URLSearchParams(clean)}` : "";
};

export const api = {
  state: () => request("GET", "/links/api/state"),
  links: {
    list: (filter) => request("GET", `/links/api/links${qs(filter)}`),
    get: (id) => request("GET", `/links/api/links/${id}`),
    ask: (id, data) => request("POST", `/links/api/links/${id}/ask`, data),
    create: (data) => request("POST", "/links/api/links", data),
    update: (id, data) => request("PATCH", `/links/api/links/${id}`, data),
    remove: (id) => request("DELETE", `/links/api/links/${id}`),
    read: (id) => request("POST", `/links/api/links/${id}/read`),
    unread: (id) => request("POST", `/links/api/links/${id}/unread`),
    archive: (id, archived = true) => request("POST", `/links/api/links/${id}/archive`, { archived }),
    favorite: (id, favorite = true) => request("POST", `/links/api/links/${id}/favorite`, { favorite }),
    refetch: (id) => request("POST", `/links/api/links/${id}/refetch`),
    video: (id) => request("GET", `/links/api/links/${id}/video`),
    analyzeVideo: (id, data = {}) => request("POST", `/links/api/links/${id}/video/analyze`, data),
    uploadVideo: async (id, file) => {
      const response = await fetch(`/links/api/links/${id}/video/file?name=${encodeURIComponent(file.name)}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file });
      let data = null;
      try { data = await response.json(); } catch { data = null; }
      if (!response.ok) throw new Error(data?.error || `Error ${response.status}`);
      return data;
    },
  },
  highlights: {
    list: (linkId) => request("GET", `/links/api/links/${linkId}/highlights`),
    create: (linkId, data) => request("POST", `/links/api/links/${linkId}/highlights`, data),
    update: (id, data) => request("PATCH", `/links/api/highlights/${id}`, data),
    remove: (id) => request("DELETE", `/links/api/highlights/${id}`),
    toCards: (id, deck) => request("POST", `/links/api/highlights/${id}/to-cards`, deck ? { deck } : {}),
    linkToCards: (linkId, deck) => request("POST", `/links/api/links/${linkId}/cards`, deck ? { deck } : {}),
  },
  videoSearch: (q) => request("GET", `/links/api/video/search${qs({ q })}`),
  videoDigest: {
    start: (data) => request("POST", "/links/api/video-digests", data),
    list: () => request("GET", "/links/api/video-digests"),
    get: (id, level, range = {}) => request("GET", `/links/api/links/${id}/digest${qs({ level, ...range })}`),
    run: (id, data = {}) => request("POST", `/links/api/links/${id}/digest`, data),
    ask: (id, question) => request("POST", `/links/api/links/${id}/digest/ask`, { question }),
    search: (q, id) => request("GET", `/links/api/video-digests/search${qs({ q, id })}`),
    settings: () => request("GET", "/links/api/video-digests/settings"),
    saveSettings: (data) => request("PUT", "/links/api/video-digests/settings", data),
    fileUrl: (id, name) => `/links/api/links/${id}/digest/files/${name}`,
    frameUrl: (id, t, width = 960) => `/links/api/links/${id}/digest/frame${qs({ t, width })}`,
  },
  resurface: (count) => request("GET", `/links/api/resurface${qs({ count })}`),
  settings: {
    get: () => request("GET", "/links/api/settings"),
    save: (data) => request("POST", "/links/api/settings", data),
  },
  watches: {
    list: () => request("GET", "/links/api/watches"),
    add: (data) => request("POST", "/links/api/watches", data),
    update: (id, data) => request("PATCH", `/links/api/watches/${id}`, data),
    remove: (id) => request("DELETE", `/links/api/watches/${id}`),
    check: (id) => request("POST", `/links/api/watches/${id}/check`),
    checkDue: () => request("POST", "/links/api/watches/check"),
    items: (filter) => request("GET", `/links/api/watch-items${qs(filter)}`),
    dismiss: (id, dismissed = true) => request("POST", `/links/api/watch-items/${id}/${dismissed ? "dismiss" : "undismiss"}`),
  },
  media: {
    list: (filter) => request("GET", `/links/api/media${qs(filter)}`),
    get: (id) => request("GET", `/links/api/media/${id}`),
    start: (data) => request("POST", "/links/api/media", data),
    cancel: (id) => request("POST", `/links/api/media/${id}/cancel`),
    retry: (id) => request("POST", `/links/api/media/${id}/retry`),
    remove: (id, files = false) => request("DELETE", `/links/api/media/${id}${files ? "?files=1" : ""}`),
    reveal: (id, i = 0) => request("POST", `/links/api/media/${id}/reveal`, { i }),
    tools: (refresh = false) => request("GET", `/links/api/media/tools${refresh ? "?refresh=1" : ""}`),
    updateTools: () => request("POST", "/links/api/media/tools/update", {}),
    settings: () => request("GET", "/links/api/media/settings"),
    saveSettings: (data) => request("PUT", "/links/api/media/settings", data),
    fileUrl: (id, i = 0) => `/links/api/media/${id}/file?i=${i}`,
  },
  sessions: {
    list: () => request("GET", "/links/api/sessions"),
    login: (site) => request("POST", `/links/api/sessions/${encodeURIComponent(site)}/login`, {}),
    finish: () => request("POST", "/links/api/sessions/finish", {}),
    cancel: () => request("POST", "/links/api/sessions/cancel", {}),
    logout: (site) => request("DELETE", `/links/api/sessions/${encodeURIComponent(site)}`),
    setBrowser: (browser) => request("PUT", "/links/api/sessions/settings", { browser }),
  },
  tags: () => request("GET", "/links/api/tags"),
  sites: () => request("GET", "/links/api/sites"),
  import: (data) => request("POST", "/links/api/import", data),
  digest: (since) => request("GET", `/links/api/digest${qs({ since })}`),
};
