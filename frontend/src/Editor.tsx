import { useEffect, useState, useRef } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, rectSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import {
  DocMeta, PageInfo, RedactArea,
  fetchFile, detectBlanks, editDoc, compressDoc, removeBlanks, downloadUrl,
  splitDoc, extractDoc, redactDoc, protectDoc, unlockDoc,
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
  const [redactMode, setRedactMode] = useState(false);
  const [redactions, setRedactions] = useState<RedactArea[]>([]);
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

  async function doSplit() {
    const spec = window.prompt(
      "Podaj zakresy stron oddzielone przecinkami (każda grupa = osobny PDF).\n" +
      "Przykłady:\n" +
      "  1-3,5,7-9   → trzy PDF-y: [1-3], [5], [7-9]\n" +
      "  *           → każda strona osobno\n" +
      "  /5          → grupy po 5 stron",
      "1-3,4-6"
    );
    if (!spec) return;
    setBusy(true); setBusyMsg("Dzielenie…"); setErr("");
    try {
      let payload: any;
      if (spec.trim() === "*") payload = { mode: "single" };
      else if (spec.startsWith("/")) payload = { mode: "every", every: parseInt(spec.slice(1)) || 1 };
      else payload = { mode: "ranges", ranges_spec: spec };
      const r = await splitDoc(doc.id, payload);
      onChanged();
      alert(`Utworzono ${r.parts.length} dokumentów. Zobacz listę.`);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function doExtract() {
    const spec = window.prompt(
      `Strony do wycięcia (1-${pages.length}).\nPrzykład: 1-3,5,7-9`,
      "1-3"
    );
    if (!spec) return;
    setBusy(true); setBusyMsg("Wycinanie…"); setErr("");
    try {
      await extractDoc(doc.id, { ranges_spec: spec });
      onChanged();
      alert("Nowy dokument utworzony — zobacz listę.");
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function doProtect() {
    const pwd = window.prompt("Hasło (min 4 znaki) — ZAPISZ je sobie, nie da się odzyskać:");
    if (!pwd) return;
    if (pwd.length < 4) { alert("Hasło za krótkie"); return; }
    const confirm2 = window.prompt("Powtórz hasło:");
    if (confirm2 !== pwd) { alert("Hasła nie pasują"); return; }
    setBusy(true); setBusyMsg("Szyfrowanie…"); setErr("");
    try { await protectDoc(doc.id, pwd); await reload(); onChanged(); alert("Plik zaszyfrowany."); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function doUnlock() {
    const pwd = window.prompt("Hasło do PDF:");
    if (pwd === null) return;
    setBusy(true); setBusyMsg("Odszyfrowywanie…"); setErr("");
    try { await unlockDoc(doc.id, pwd); await reload(); onChanged(); alert("Hasło usunięte."); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  async function applyRedactions() {
    if (!redactions.length) { alert("Brak zaznaczonych obszarów"); return; }
    if (!window.confirm(`Wymazać ${redactions.length} obszar(y)? Operacja nieodwracalna.`)) return;
    setBusy(true); setBusyMsg("Wymazywanie…"); setErr("");
    try {
      await redactDoc(doc.id, redactions);
      setRedactions([]);
      setRedactMode(false);
      await reload();
      onChanged();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); setBusyMsg(""); }
  }

  return (
    <div>
      <div className="toolbar">
        <button onClick={onBack}>← Wróć</button>
        <strong style={{ alignSelf: "center" }}>{doc.filename}</strong>
        <span style={{ alignSelf: "center", color: "#666" }}>
          {pages.length} stron, {(doc.size_bytes / 1024).toFixed(0)} KB
          {doc.is_encrypted && <span style={{ color: "#c0392b", marginLeft: 8 }}>🔒 ZASZYFROWANY</span>}
        </span>
      </div>

      {doc.is_encrypted ? (
        <div className="toolbar">
          <div style={{ padding: "1rem", color: "#c0392b" }}>
            Plik zaszyfrowany hasłem — odblokuj przed edycją.
          </div>
          <button onClick={doUnlock} disabled={busy}>🔓 Zdejmij hasło</button>
          <a href={downloadUrl(doc.id)}><button>Pobierz (zaszyfrowany)</button></a>
        </div>
      ) : (
        <>
          <div className="toolbar">
            <button onClick={() => rotateSelected(90)} disabled={!selected.size || busy || redactMode}>Obróć ↻ 90°</button>
            <button onClick={() => rotateSelected(-90)} disabled={!selected.size || busy || redactMode}>Obróć ↺ 90°</button>
            <button onClick={() => rotateSelected(180)} disabled={!selected.size || busy || redactMode}>Obróć 180°</button>
            <button onClick={deleteSelected} disabled={!selected.size || busy || redactMode}>Usuń zaznaczone ({selected.size})</button>
            <button onClick={selectBlanks} disabled={busy || redactMode}>Zaznacz puste</button>
            <button onClick={autoRemoveBlanks} disabled={busy || redactMode}>Auto-usuń puste</button>
            <button onClick={() => applyEdits(false)} disabled={busy || !dirty || redactMode}>
              {dirty ? "Zapisz zmiany*" : "Zapisz zmiany"}
            </button>
            <button onClick={() => applyEdits(true)} disabled={busy || redactMode}>Zapisz + kompresuj</button>
            <button onClick={compressOnly} disabled={busy || redactMode}>Kompresuj</button>
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

          <div className="toolbar">
            <strong style={{ alignSelf: "center", marginRight: 4 }}>Więcej:</strong>
            <button onClick={doSplit} disabled={busy || redactMode}>✂ Podziel</button>
            <button onClick={doExtract} disabled={busy || redactMode}>📄 Wytnij strony</button>
            <button onClick={() => { setRedactMode(m => !m); setRedactions([]); }} disabled={busy}>
              {redactMode ? "✕ Anuluj wymazywanie" : "⬛ Wymaż obszary"}
            </button>
            <button onClick={doProtect} disabled={busy || redactMode}>🔒 Ustaw hasło</button>
            <button onClick={doUnlock} disabled={busy || redactMode} title="Tylko jeśli plik jest już zaszyfrowany">
              🔓 Zdejmij hasło
            </button>
          </div>
        </>
      )}

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

      {blob && !doc.is_encrypted && !redactMode && (
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

      {blob && redactMode && (
        <RedactView
          blob={blob}
          totalPages={pages.length}
          redactions={redactions}
          setRedactions={setRedactions}
          onApply={applyRedactions}
          busy={busy}
        />
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

interface RedactViewProps {
  blob: Blob;
  totalPages: number;
  redactions: RedactArea[];
  setRedactions: (r: RedactArea[] | ((prev: RedactArea[]) => RedactArea[])) => void;
  onApply: () => void;
  busy: boolean;
}

function RedactView({ blob, totalPages, redactions, setRedactions, onApply, busy }: RedactViewProps) {
  const [pageIdx, setPageIdx] = useState(0); // 0-based
  const [width] = useState(700);
  const overlayRef = useRef<HTMLDivElement>(null);
  const [drawing, setDrawing] = useState<null | { x0: number; y0: number; x: number; y: number }>(null);

  function onMouseDown(e: React.MouseEvent) {
    if (!overlayRef.current) return;
    const rect = overlayRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setDrawing({ x0: x, y0: y, x, y });
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!drawing || !overlayRef.current) return;
    const rect = overlayRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setDrawing({ ...drawing, x, y });
  }
  function onMouseUp() {
    if (!drawing) return;
    const x = Math.min(drawing.x0, drawing.x);
    const y = Math.min(drawing.y0, drawing.y);
    const w = Math.abs(drawing.x - drawing.x0);
    const h = Math.abs(drawing.y - drawing.y0);
    setDrawing(null);
    if (w < 0.005 || h < 0.005) return; // ignore tiny clicks
    setRedactions(prev => [...prev, { page: pageIdx, x, y, w, h }]);
  }

  const onPage = redactions.filter(r => r.page === pageIdx);

  return (
    <div style={{ marginTop: "1rem" }}>
      <div className="toolbar">
        <button onClick={() => setPageIdx(i => Math.max(0, i - 1))} disabled={pageIdx === 0 || busy}>◄ Poprzednia</button>
        <strong style={{ alignSelf: "center" }}>Strona {pageIdx + 1} / {totalPages}</strong>
        <button onClick={() => setPageIdx(i => Math.min(totalPages - 1, i + 1))} disabled={pageIdx >= totalPages - 1 || busy}>Następna ►</button>
        <span style={{ alignSelf: "center", marginLeft: "1rem", color: "#666" }}>
          Zaznacz obszary myszką (klik + przeciągnij). Zaznaczono: {redactions.length}
        </span>
        <button onClick={() => setRedactions(prev => prev.filter(r => r.page !== pageIdx))}
                disabled={!onPage.length || busy}>Wyczyść tę stronę</button>
        <button onClick={() => setRedactions([])} disabled={!redactions.length || busy}>Wyczyść wszystko</button>
        <button onClick={onApply} disabled={!redactions.length || busy}
                style={{ background: "#c0392b", color: "white", fontWeight: "bold" }}>
          ⬛ ZASTOSUJ ({redactions.length})
        </button>
      </div>

      <div style={{ position: "relative", display: "inline-block", border: "2px solid #c0392b" }}>
        <Document file={blob}>
          <Page pageNumber={pageIdx + 1} width={width} renderTextLayer={false} renderAnnotationLayer={false} />
        </Document>
        <div
          ref={overlayRef}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={() => setDrawing(null)}
          style={{
            position: "absolute", inset: 0, cursor: "crosshair", userSelect: "none",
          }}
        >
          {onPage.map((r, idx) => (
            <div key={idx}
              style={{
                position: "absolute",
                left: `${r.x * 100}%`, top: `${r.y * 100}%`,
                width: `${r.w * 100}%`, height: `${r.h * 100}%`,
                background: "rgba(0,0,0,0.85)",
                border: "1px solid red",
              }}
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm("Usunąć ten obszar?")) {
                  setRedactions(prev => prev.filter(x => x !== r));
                }
              }}
              title="Kliknij aby usunąć"
            />
          ))}
          {drawing && (
            <div style={{
              position: "absolute",
              left: `${Math.min(drawing.x0, drawing.x) * 100}%`,
              top: `${Math.min(drawing.y0, drawing.y) * 100}%`,
              width: `${Math.abs(drawing.x - drawing.x0) * 100}%`,
              height: `${Math.abs(drawing.y - drawing.y0) * 100}%`,
              background: "rgba(0,0,0,0.5)",
              border: "1px dashed yellow",
            }} />
          )}
        </div>
      </div>
    </div>
  );
}
