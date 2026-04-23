import { useEffect, useState, useRef } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, rectSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import {
  DocMeta, PageInfo, fetchFile, detectBlanks, editDoc, compressDoc, removeBlanks, downloadUrl,
} from "./api";

// pdf.js worker — bundled locally (offline)
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

interface PageState {
  srcIndex: number;   // original page index in current backend doc
  rotation: number;   // accumulated 0/90/180/270
  blank: boolean;
}

export function Editor({ doc, onChanged, onBack }: { doc: DocMeta; onChanged: () => void; onBack: () => void }) {
  const [blob, setBlob] = useState<Blob | null>(null);
  const [pages, setPages] = useState<PageState[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [busyMsg, setBusyMsg] = useState("");
  const [dirty, setDirty] = useState(false);
  const [compressPreset, setCompressPreset] = useState("medium");
  const [err, setErr] = useState("");
  const reloadKey = useRef(0);

  async function reload() {
    setBusy(true); setBusyMsg("Wczytywanie…"); setErr("");
    try {
      const b = await fetchFile(doc.id);
      setBlob(b);
      const blanks = await detectBlanks(doc.id);
      const map = new Map<number, PageInfo>(blanks.map(p => [p.index, p]));
      setPages(Array.from({ length: doc.page_count }, (_, i) => ({
        srcIndex: i,
        rotation: 0,
        blank: map.get(i)?.blank ?? false,
      })));
      setSelected(new Set());
      setDirty(false);
      reloadKey.current++;
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [doc.id]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function onDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIdx = pages.findIndex(p => keyOf(p) === active.id);
    const newIdx = pages.findIndex(p => keyOf(p) === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    setPages(arrayMove(pages, oldIdx, newIdx));
    setDirty(true);
  }

  function keyOf(p: PageState) { return `p-${p.srcIndex}`; }

  function toggleSelect(srcIndex: number) {
    setSelected(s => {
      const n = new Set(s);
      n.has(srcIndex) ? n.delete(srcIndex) : n.add(srcIndex);
      return n;
    });
  }

  function rotateSelected(deg: number) {
    setPages(ps => ps.map(p => selected.has(p.srcIndex)
      ? { ...p, rotation: (p.rotation + deg + 360) % 360 } : p));
    setDirty(true);
  }

  function deleteSelected() {
    if (!selected.size) return;
    setPages(ps => ps.filter(p => !selected.has(p.srcIndex)));
    setSelected(new Set());
    setDirty(true);
  }

  function selectBlanks() {
    setSelected(new Set(pages.filter(p => p.blank).map(p => p.srcIndex)));
  }

  async function applyEdits(compress: boolean) {
    setBusy(true); setBusyMsg(compress ? "Zapis + kompresja…" : "Zapis…"); setErr("");
    try {
      const keep_order = pages.map(p => p.srcIndex);
      const rotations: Record<string, number> = {};
      for (const p of pages) if (p.rotation) rotations[String(p.srcIndex)] = p.rotation;
      await editDoc(doc.id, { keep_order, rotations, compress, gs_quality: compressPreset });
      await reload();
      onChanged();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function autoRemoveBlanks() {
    setBusy(true); setBusyMsg("Usuwam puste strony…");
    try { await removeBlanks(doc.id); await reload(); onChanged(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function compressOnly() {
    setBusy(true); setBusyMsg(`Kompresja (${compressPreset})…`);
    try { await compressDoc(doc.id, compressPreset); await reload(); onChanged(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  return (
    <div>
      <div className="toolbar">
        <button onClick={onBack}>← Wróć</button>
        <strong style={{ alignSelf: "center" }}>{doc.filename}</strong>
        <span style={{ alignSelf: "center", color: "#666" }}>
          {pages.length} stron, {(doc.size_bytes / 1024).toFixed(0)} KB
        </span>
      </div>

      <div className="toolbar">
        <button onClick={() => rotateSelected(90)} disabled={!selected.size || busy}>Obróć ↻ 90°</button>
        <button onClick={() => rotateSelected(-90)} disabled={!selected.size || busy}>Obróć ↺ 90°</button>
        <button onClick={() => rotateSelected(180)} disabled={!selected.size || busy}>Obróć 180°</button>
        <button onClick={deleteSelected} disabled={!selected.size || busy}>Usuń zaznaczone ({selected.size})</button>
        <button onClick={selectBlanks} disabled={busy}>Zaznacz puste</button>
        <button onClick={autoRemoveBlanks} disabled={busy}>Auto-usuń puste</button>
        <button onClick={() => applyEdits(false)} disabled={busy || !dirty}>
          {dirty ? "Zapisz zmiany*" : "Zapisz zmiany"}
        </button>
        <button onClick={() => applyEdits(true)} disabled={busy}>Zapisz + kompresuj</button>
        <button onClick={compressOnly} disabled={busy}>Kompresuj</button>
        <select value={compressPreset} onChange={e => setCompressPreset(e.target.value)} disabled={busy}>
          <option value="low">Niska (lekka, zachowuje jakość)</option>
          <option value="medium">Średnia (domyślnie)</option>
          <option value="high">Wysoka (100 DPI, q=60)</option>
          <option value="extreme">Ekstremalna (72 DPI, q=45)</option>
          <option value="rasterize">Rasteryzacja (max, traci tekst!)</option>
        </select>
        {busy || dirty ? (
          <button disabled title={dirty ? "Zapisz najpierw zmiany" : "Trwa operacja"}>Pobierz</button>
        ) : (
          <a href={downloadUrl(doc.id)}><button>Pobierz</button></a>
        )}
      </div>

      {(err || busyMsg) && (
        <div style={{ margin: ".5rem 0" }}>
          {busyMsg && <div style={{ color: "#2980b9" }}>⏳ {busyMsg}</div>}
          {err && <div className="error">{err}</div>}
        </div>
      )}
      {dirty && !busy && (
        <div style={{ color: "#e67e22", margin: ".5rem 0" }}>
          Niezapisane zmiany — kliknij „Zapisz zmiany" przed pobraniem.
        </div>
      )}

      {blob && (
        <Document file={blob} key={reloadKey.current}>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={pages.map(keyOf)} strategy={rectSortingStrategy}>
              <div className="grid">
                {pages.map((p, displayIdx) => (
                  <SortablePage
                    key={keyOf(p)}
                    id={keyOf(p)}
                    page={p}
                    displayIdx={displayIdx}
                    selected={selected.has(p.srcIndex)}
                    onClick={() => toggleSelect(p.srcIndex)}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </Document>
      )}
    </div>
  );
}

function SortablePage({
  id, page, displayIdx, selected, onClick,
}: { id: string; page: PageState; displayIdx: number; selected: boolean; onClick: () => void; }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };
  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`page-card ${page.blank ? "blank" : ""} ${selected ? "selected" : ""}`}
      onClick={onClick}
      {...attributes}
      {...listeners}
    >
      {page.blank && <span className="badge">PUSTA</span>}
      <Page pageNumber={page.srcIndex + 1} width={160} rotate={page.rotation} renderTextLayer={false} renderAnnotationLayer={false} />
      <div className="num">#{displayIdx + 1} (orig {page.srcIndex + 1}){page.rotation ? ` ${page.rotation}°` : ""}</div>
    </div>
  );
}
