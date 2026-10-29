import React, { useMemo, useRef, useState } from "react";
import {
  FileSpreadsheet, FileUp, Download, Eye, RefreshCw, AlertCircle,
  CheckCircle2, FileText, X, LoaderCircle, Table2
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export default function App() {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [pagesToExtract, setPagesToExtract] = useState("10");
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [view, setView] = useState("counts");
  const [filter, setFilter] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [progressMessage, setProgressMessage] = useState("");

  const resetResult = () => {
    setResult(null);
    setError("");
    setStatus("idle");
    setProgressMessage("");
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    setDownloadUrl("");
  };

  const chooseFile = (nextFile) => {
    if (!nextFile) return;
    if (!nextFile.name.toLowerCase().endsWith(".pdf")) {
      setError("Please choose a PDF file.");
      return;
    }
    resetResult();
    setFile(nextFile);
    setError("");
  };

  const runExtraction = async () => {
    if (!file) {
      setError("Choose a PDF before starting extraction.");
      return;
    }
    const requestedPages = Number(pagesToExtract);
    if (!Number.isInteger(requestedPages) || requestedPages < 1) {
      setError("Enter a whole number of pages greater than or equal to 1.");
      return;
    }
    setStatus("processing");
    setError("");
    setProgressMessage("Uploading PDF and extracting MCB ratings…");

    const body = new FormData();
    body.append("file", file);
    body.append("pages", String(requestedPages));

    try {
      const response = await fetch(`${API_BASE}/api/extract`, {
        method: "POST",
        body
      });
      if (!response.ok) {
        let message = `Extraction failed (HTTP ${response.status}).`;
        try {
          const payload = await response.json();
          message = payload.detail || payload.error || message;
        } catch {}
        throw new Error(message);
      }

      const payload = await response.json();
      if (!payload.columns || !payload.rows) {
        throw new Error("The server response did not contain 'columns' and 'rows'.");
      }
      setResult(payload);
      setView("counts");
      setStatus("complete");
      setProgressMessage("");
      if (payload.xlsx_base64) {
        const binary = atob(payload.xlsx_base64);
        const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
        setDownloadUrl(URL.createObjectURL(new Blob(
          [bytes],
          { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }
        )));
      } else if (payload.download_url) {
        setDownloadUrl(`${API_BASE}${payload.download_url}`);
      }
    } catch (err) {
      setStatus("error");
      setError(err.message || "Something went wrong while extracting the PDF.");
      setProgressMessage("");
    }
  };

  const downloadExcel = async () => {
    if (downloadUrl) {
      const a = document.createElement("a");
      a.href = downloadUrl;
      a.download = "mcb_counts.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      return;
    }
    if (result?.download_url) {
      window.location.href = `${API_BASE}${result.download_url}`;
      return;
    }
    setError("The server did not provide an Excel download.");
  };

  const visibleRows = useMemo(() => {
    if (!result?.rows) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return result.rows;
    return result.rows.filter((row) =>
      String(row[result.row_header || "MCB Rating"] ?? "").toLowerCase().includes(q)
    );
  }, [result, filter]);

  const pagesCount = result?.columns?.length
    ? result.columns.filter((c) => c !== (result.row_header || "MCB Rating")).length
    : 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><FileSpreadsheet size={22} /></div>
        <div className="brand-copy">
          <div className="brand-name">PanelWise</div>
          <div className="brand-subtitle">Electrical schedule tools</div>
        </div>
        <div className="topbar-right"><span className="status-dot" /> Local workspace</div>
      </header>

      <main className="main-content">
        <section className="hero">
          <div className="eyebrow">PDF → EXCEL</div>
          <h1>MCB Schedule Extractor</h1>
          <p>Upload a scanned distribution board load schedule. Extract MCB counts by rating for each DB reference and review the results before downloading.</p>
        </section>

        <section className="workspace">
          <div className="section-heading">
            <div>
              <h2>Upload document</h2>
              <p>PDF files only · Choose how many pages to process, starting from page 1</p>
            </div>
            <span className="step-pill">STEP 01</span>
          </div>

          <div
            className={`dropzone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault(); setDragging(false);
              chooseFile(e.dataTransfer.files?.[0]);
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,application/pdf"
              hidden
              onChange={(e) => chooseFile(e.target.files?.[0])}
            />
            <div className="upload-icon"><FileUp size={26} /></div>
            {file ? (
              <>
                <div className="file-name">{file.name}</div>
                <div className="muted">{(file.size / (1024 * 1024)).toFixed(2)} MB · Ready to process</div>
                <button className="text-button" onClick={() => { setFile(null); resetResult(); if (inputRef.current) inputRef.current.value = ""; }}>
                  <X size={14} /> Remove file
                </button>
              </>
            ) : (
              <>
                <div className="drop-title">Drop your PDF here</div>
                <div className="muted">or browse files on your computer</div>
                <button className="secondary-button" onClick={() => inputRef.current?.click()}>Choose PDF</button>
              </>
            )}
          </div>

          <div className="page-count-control">
            <label htmlFor="pages-to-extract">Number of pages to extract</label>
            <input
              id="pages-to-extract"
              type="number"
              min="1"
              max="10000"
              step="1"
              inputMode="numeric"
              value={pagesToExtract}
              onChange={(e) => setPagesToExtract(e.target.value)}
              disabled={status === "processing"}
              aria-describedby="pages-help"
            />
            <p id="pages-help">Processing starts at PDF page 1. If the PDF has fewer pages than requested, all available pages are processed.</p>
          </div>

          <div className="actions-row">
            <div className="privacy-note"><AlertCircle size={15} /> Processing depends on the connected Python API.</div>
            <button className="primary-button" disabled={!file || status === "processing"} onClick={runExtraction}>
              {status === "processing" ? <><LoaderCircle className="spin" size={17} /> Processing…</> : <><Table2 size={17} /> Extract MCB counts</>}
            </button>
          </div>

          {status === "processing" && (
            <div className="progress-card">
              <LoaderCircle className="spin" size={20} />
              <div><strong>Processing your schedule</strong><p>{progressMessage || "This may take a few minutes for a large scanned PDF."}</p></div>
            </div>
          )}
          {error && (
            <div className="error-card"><AlertCircle size={18} /><div><strong>Something needs attention</strong><p>{error}</p><small>Confirm that the Python backend is running at {API_BASE}.</small></div></div>
          )}
          {status === "complete" && (
            <div className="success-card"><CheckCircle2 size={18} /><div><strong>Extraction completed</strong><p>Review the counts and validation notes before using the results.</p></div></div>
          )}
        </section>

        {result && (
          <section className="results-section">
            <div className="section-heading results-heading">
              <div><h2>Extraction results</h2><p>Counts grouped by board ratings and DB reference</p></div>
              <span className="step-pill">STEP 02</span>
            </div>

            <div className="summary-grid">
              <div className="summary-card"><span>DB columns</span><strong>{pagesCount}</strong><small>Detected references</small></div>
              <div className="summary-card"><span>MCB ratings</span><strong>{result.rows.length}</strong><small>Distinct rating rows</small></div>
              <div className="summary-card"><span>Pages flagged</span><strong>{result.review?.length ?? 0}</strong><small>Review recommended</small></div>
            </div>

            <div className="table-toolbar">
              <div className="view-switch">
                <button className={view === "counts" ? "active" : ""} onClick={() => setView("counts")}><Table2 size={15}/> Counts</button>
                <button className={view === "review" ? "active" : ""} onClick={() => setView("review")}><Eye size={15}/> Review</button>
              </div>
              {view === "counts" && <input className="search-input" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter rating (e.g. 20 A)" />}
              <button className="download-button" onClick={downloadExcel}><Download size={16}/> Download Excel</button>
            </div>

            {view === "counts" ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr>{result.columns.map((col, i) => <th key={`${col}-${i}`} className={i === 0 ? "sticky-col" : ""}>{col}</th>)}</tr></thead>
                  <tbody>
                    {visibleRows.map((row, i) => (
                      <tr key={i}>{result.columns.map((col, j) => <td key={`${col}-${j}`} className={j === 0 ? "sticky-col rating-cell" : "numeric-cell"}>{row[col] ?? 0}</td>)}</tr>
                    ))}
                    {visibleRows.length === 0 && <tr><td colSpan={result.columns.length} className="empty-cell">No matching board ratings.</td></tr>}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="table-wrap">
                <table className="data-table review-table">
                  <thead><tr>{(result.review_columns || ["PDF page", "DB REF NO", "Detected Board entries", "Review note"]).map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>
                    {(result.review || []).map((row, i) => (
                      <tr key={i}>{(result.review_columns || ["PDF page", "DB REF NO", "Detected Board entries", "Review note"]).map((c) => <td key={c}>{row[c] ?? ""}</td>)}</tr>
                    ))}
                    {(!result.review || result.review.length === 0) && <tr><td colSpan={4} className="empty-cell">No review records were returned.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
            <div className="results-footnote"><AlertCircle size={15}/> OCR-generated counts should be checked against the original schedules, especially where review notes are present.</div>
          </section>
        )}
        <footer>PanelWise · MCB Schedule Extractor <span>Built for scanned electrical load schedules</span></footer>
      </main>
    </div>
  );
}
