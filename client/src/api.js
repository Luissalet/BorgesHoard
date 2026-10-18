// Thin fetch wrapper: JSON in/out, `{ error }` bodies become exceptions.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) throw new Error((data && data.error) || `Error ${response.status}`);
  return data;
}

export const api = {
  status: () => request("GET", "/library/api/status"),
  collections: () => request("GET", "/library/api/collections"),
  collection: (id) => request("GET", `/library/api/collections/${id}`),
  addCollection: (body) => request("POST", "/library/api/collections", { body }),
  updateCollection: (id, patch) => request("PATCH", `/library/api/collections/${id}`, { body: patch }),
  removeCollection: (id) => request("DELETE", `/library/api/collections/${id}`),
  reindex: (id) => request("POST", `/library/api/collections/${id}/reindex`),
  documents: (params) => request("GET", "/library/api/documents", { params }),
  document: (id) => request("GET", `/library/api/documents/${id}`),
  text: (id, params) => request("GET", `/library/api/documents/${id}/text`, { params }),
  search: (params) => request("GET", "/library/api/search", { params }),
  similar: (chunkId, limit = 8) => request("GET", `/library/api/similar/${chunkId}`, { params: { limit } }),
  chunk: (chunkId) => request("GET", `/library/api/chunks/${chunkId}`),
  sources: () => request("GET", "/library/api/sources"),
  addSource: (body) => request("POST", "/library/api/sources", { body }),
  addLinksSource: (body) => request("POST", "/library/api/sources/links", { body }),
  updateSource: (id, patch) => request("PATCH", `/library/api/sources/${id}`, { body: patch }),
  removeSource: (id) => request("DELETE", `/library/api/sources/${id}`),
  syncSource: (id) => request("POST", `/library/api/sources/${id}/sync`),
};
