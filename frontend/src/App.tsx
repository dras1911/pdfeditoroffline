import { useEffect, useState } from "react";
import { Editor } from "./Editor";
import {
  DocMeta, listDocs, uploadDoc, deleteDoc,
  mergeDocs, imagesToPdf,
} from "./api";

export function App() {
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [active, setActive] = useState<DocMeta | null>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [persist, setPersist] = useState(false);
  const [err, setErr] = useState("");
  const [mergeSel, setMergeSel] = useState<string[]>([]);

  async function refresh() {
    try { setDocs(await listDocs()); }
    catch (e: any) { setErr(e.message); }
  }

  useEffect(() => { refresh(); }, []);

  async function handleFiles(files: FileList | File[]) {
    setBusy(true); setErr("");
    try {
      const arr = Array.from(files);
      const pdfs = arr.filter(f => f.name.toLowerCase().endsWith(".pdf"));
      const imgs = arr.filter(f => /\.(jpe?g|png|tiff?|bmp|webp)$/i.test(f.name));
      for (const f of pdfs) await uploadDoc(f, persist);
      if (imgs.length) {
        await imagesToPdf(imgs, `images_${Date.now()}.pdf`, persist);
      }
      await refresh();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function remove(id: string) {
    if (!confirm("Usunąć dokument?")) return;
    await deleteDoc(id);
    if (active?.id === id) setActive(null);
    setMergeSel(s => s.filter(x => x !== id));
    refresh();
  }

  function toggleMerge(id: string) {
    setMergeSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  }

  async function doMerge() {
    if (mergeSel.length < 2) return;
    setBusy(true); setErr("");
    try {
      const name = prompt("Nazwa wynikowego PDF:", `merged_${Date.now()}.pdf`) || undefined;
      await mergeDocs(mergeSel, name, persist);
      setMergeSel([]);
      await refresh();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="app">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>PDF Tools (offline)</h1>
      </div>

      {!active && (
        <>
          <div
            className={`dropzone ${drag ? "drag" : ""}`}
            onDragOver={e => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={e => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}
          >
            <p>Przeciągnij PDF lub obrazy (jpg/png/tiff/bmp/webp) tutaj lub wybierz:</p>
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/tiff,image/bmp,image/webp"
              multiple
              onChange={e => e.target.files && handleFiles(e.target.files)}
            />
            <div style={{ marginTop: ".7rem" }}>
              <label>
                <input type="checkbox" checked={persist} onChange={e => setPersist(e.target.checked)} />
                {" "}Zapisz roboczo (auto-purge po 24h)
              </label>
            </div>
            <p style={{ fontSize: ".8rem", color: "#666", marginTop: ".5rem" }}>
              Obrazy → automatyczna konwersja do jednego PDF (kolejność = wybór).
            </p>
            {busy && <div>Pracuję…</div>}
            {err && <div className="error">{err}</div>}
          </div>

          <div className="docs-list" style={{ marginTop: "1rem" }}>
            <h2>Twoje dokumenty</h2>
            <div className="toolbar">
              <button onClick={doMerge} disabled={mergeSel.length < 2 || busy}>
                Połącz zaznaczone ({mergeSel.length})
              </button>
              {mergeSel.length > 0 && <button onClick={() => setMergeSel([])}>Wyczyść zaznaczenie</button>}
              <span style={{ alignSelf: "center", color: "#666", fontSize: ".85rem" }}>
                Kolejność łączenia = kolejność klikania.
              </span>
            </div>
            {docs.length === 0 && <p>Brak.</p>}
            {docs.length > 0 && (
              <table>
                <thead>
                  <tr><th></th><th>Nazwa</th><th>Stron</th><th>Rozmiar</th><th>Tryb</th><th>Utworzony</th><th></th></tr>
                </thead>
                <tbody>
                  {docs.map(d => {
                    const idx = mergeSel.indexOf(d.id);
                    return (
                      <tr key={d.id}>
                        <td>
                          <label title="Zaznacz do łączenia">
                            <input type="checkbox" checked={idx >= 0} onChange={() => toggleMerge(d.id)} />
                            {idx >= 0 && <span style={{ marginLeft: 4, color: "#2980b9" }}>{idx + 1}</span>}
                          </label>
                        </td>
                        <td>{d.filename}</td>
                        <td>{d.page_count}</td>
                        <td>{(d.size_bytes / 1024).toFixed(0)} KB</td>
                        <td>{d.persist ? "robocze" : "sesja"}</td>
                        <td>{new Date(d.created_at).toLocaleString("pl-PL")}</td>
                        <td>
                          <button onClick={() => setActive(d)}>Edytuj</button>{" "}
                          <a href={`/api/docs/${d.id}/export-images?fmt=png&dpi=150`}>
                            <button>Export PNG</button>
                          </a>{" "}
                          <button onClick={() => remove(d.id)}>Usuń</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {active && <Editor doc={active} onChanged={refresh} onBack={() => { setActive(null); refresh(); }} />}
    </div>
  );
}
