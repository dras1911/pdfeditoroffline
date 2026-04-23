async function req(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(path, opts);
}

export interface DocMeta {
  id: string;
  filename: string;
  page_count: number;
  size_bytes: number;
  persist: boolean;
  is_encrypted?: boolean;
  created_at: string;
}

export async function listDocs(): Promise<DocMeta[]> {
  const r = await req("/api/docs");
  return r.json();
}

export async function uploadDoc(file: File, persist: boolean): Promise<DocMeta> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("persist", persist ? "true" : "false");
  const r = await req("/api/docs", { method: "POST", body: fd });
  if (!r.ok) throw new Error("upload failed");
  return r.json();
}

export async function deleteDoc(id: string) {
  await req(`/api/docs/${id}`, { method: "DELETE" });
}

export interface PageInfo { index: number; blank: boolean; reason: string; }

export async function detectBlanks(id: string): Promise<PageInfo[]> {
  const r = await req(`/api/docs/${id}/blanks`);
  const j = await r.json();
  return j.pages;
}

export async function editDoc(id: string, payload: any) {
  const r = await req(`/api/docs/${id}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error("edit failed");
  return r.json();
}

export async function compressDoc(id: string, gs_quality?: string) {
  const r = await req(`/api/docs/${id}/compress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gs_quality }),
  });
  return r.json();
}

export async function removeBlanks(id: string) {
  const r = await req(`/api/docs/${id}/remove-blanks`, { method: "POST" });
  return r.json();
}

export async function fetchFile(id: string): Promise<Blob> {
  const r = await req(`/api/docs/${id}/file`);
  return r.blob();
}

export function downloadUrl(id: string) {
  return `/api/docs/${id}/file`;
}

export function exportImagesUrl(id: string, fmt = "png", dpi = 150) {
  return `/api/docs/${id}/export-images?fmt=${fmt}&dpi=${dpi}`;
}

export async function mergeDocs(ids: string[], filename?: string, persist = false): Promise<DocMeta> {
  const r = await req("/api/docs/merge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, filename, persist }),
  });
  if (!r.ok) throw new Error("merge failed");
  return r.json();
}

export async function imagesToPdf(files: File[], filename: string, persist: boolean): Promise<DocMeta> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  if (filename) fd.append("filename", filename);
  fd.append("persist", persist ? "true" : "false");
  const r = await req("/api/docs/from-images", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "images->pdf failed");
  return r.json();
}

export interface DocMetaExt extends DocMeta { is_encrypted?: boolean; }

async function postJson(path: string, body: any) {
  const r = await req(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `${path} failed (${r.status})`);
  }
  return r.json();
}

export async function splitDoc(id: string, payload: {
  mode: "single" | "every" | "ranges";
  every?: number;
  ranges_spec?: string;
  persist?: boolean;
}): Promise<{ parts: DocMeta[] }> {
  return postJson(`/api/docs/${id}/split`, payload);
}

export async function extractDoc(id: string, payload: {
  ranges_spec?: string;
  pages?: number[];
  persist?: boolean;
  filename?: string;
}): Promise<DocMeta> {
  return postJson(`/api/docs/${id}/extract`, payload);
}

export interface RedactArea { page: number; x: number; y: number; w: number; h: number; }
export async function redactDoc(id: string, areas: RedactArea[]) {
  return postJson(`/api/docs/${id}/redact`, { areas });
}

export async function protectDoc(id: string, password: string) {
  return postJson(`/api/docs/${id}/protect`, { password });
}

export async function unlockDoc(id: string, password: string) {
  return postJson(`/api/docs/${id}/unlock`, { password });
}
