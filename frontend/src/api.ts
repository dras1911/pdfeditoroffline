async function req(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(path, opts);
}

export interface DocMeta {
  id: string;
  filename: string;
  page_count: number;
  size_bytes: number;
  persist: boolean;
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
