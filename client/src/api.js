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
  status: () => request("GET", "/api/status"),
  collections: () => request("GET", "/api/collections"),
  addCollection: (body) => request("POST", "/api/collections", { body }),
  updateCollection: (id, patch) => request("PATCH", `/api/collections/${id}`, { body: patch }),
  removeCollection: (id) => request("DELETE", `/api/collections/${id}`),
  reindex: (id) => request("POST", `/api/collections/${id}/reindex`),
  documents: (params) => request("GET", "/api/documents", { params }),
  document: (id) => request("GET", `/api/documents/${id}`),
  text: (id, params) => request("GET", `/api/documents/${id}/text`, { params }),
  search: (params) => request("GET", "/api/search", { params }),
  similar: (chunkId, limit = 8) => request("GET", `/api/similar/${chunkId}`, { params: { limit } }),
  chunk: (chunkId) => request("GET", `/api/chunks/${chunkId}`),
};
