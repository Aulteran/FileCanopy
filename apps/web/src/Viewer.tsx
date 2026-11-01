import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type ArchiveListing, type NodeInfo } from "./api";
import { describeError, formatSize } from "./format";
import { folderPath, hasInAppHistory } from "./route";
import {
  TEXT_LIMIT,
  parseDelimited,
  viewKind,
  type Table,
  type ViewKind,
} from "./viewerTypes";

// Full-page viewer (M07d, ADR-019). Everything is read from the access-checked /view endpoint
// of this origin. Nothing the file contains is ever inserted as HTML: text and table cells are
// React strings, SVG goes through an <img>, and PDF pages are drawn onto canvases.

type Props = {
  nodeId: string;
  go: (path: string, replace?: boolean) => void;
  onEnded: () => void;
};

export function Viewer({ nodeId, go, onEnded }: Props) {
  const [node, setNode] = useState<NodeInfo | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setNode(null);
    setError("");
    api
      .node(nodeId)
      .then((n) => live && setNode(n))
      .catch((err: unknown) => {
        if (!live) return;
        if (err instanceof ApiError && err.status === 401) onEnded();
        setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      });
    return () => {
      live = false;
    };
  }, [nodeId, onEnded]);

  const back = useCallback(() => {
    if (hasInAppHistory()) window.history.back();
    else go(node?.parent_id ? folderPath(node.parent_id) : "/", true);
  }, [go, node]);

  const kind: ViewKind | null =
    node && node.kind === "file" ? viewKind(node.name) : null;

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <button type="button" onClick={back}>
          ← Back
        </button>
        <h1 className="viewer-title">{node?.name ?? "Opening…"}</h1>
        {node?.kind === "file" && (
          <a className="button" href={api.downloadUrl(nodeId)} download>
            Download
          </a>
        )}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!error && node === null && <p className="loading">Loading…</p>}
      {node !== null && node.kind !== "file" && (
        <Unsupported nodeId={nodeId} reason="This is not a file." />
      )}
      {node !== null && node.kind === "file" && kind === null && (
        <Unsupported
          nodeId={nodeId}
          reason="There is no preview for this kind of file here yet."
        />
      )}
      {node !== null && kind === "pdf" && (
        <PdfView key={nodeId} nodeId={nodeId} onEnded={onEnded} />
      )}
      {node !== null && kind === "office" && (
        <OfficeView key={nodeId} nodeId={nodeId} onEnded={onEnded} />
      )}
      {node !== null && kind === "archive" && (
        <ArchiveView key={nodeId} nodeId={nodeId} onEnded={onEnded} />
      )}
      {node !== null && kind === "image" && (
        <ImageView key={nodeId} nodeId={nodeId} name={node.name} />
      )}
      {node !== null && (kind === "text" || kind === "csv") && (
        <TextView
          key={nodeId}
          nodeId={nodeId}
          onEnded={onEnded}
          delimiter={
            kind === "csv"
              ? node.name.toLowerCase().endsWith(".tsv")
                ? "\t"
                : ","
              : null
          }
        />
      )}
    </div>
  );
}

function Unsupported({ nodeId, reason }: { nodeId: string; reason: string }) {
  return (
    <section className="viewer-note" aria-live="polite">
      <h2>No preview here</h2>
      <p>{reason} You can still download it and open it on your device.</p>
      <p>
        <a className="button primary" href={api.downloadUrl(nodeId)} download>
          Download
        </a>
      </p>
    </section>
  );
}

function CouldNotShow({ nodeId, text }: { nodeId: string; text: string }) {
  return (
    <section className="viewer-note" role="alert">
      <h2>This file cannot be shown</h2>
      <p>{text} You can still download it.</p>
      <p>
        <a className="button primary" href={api.downloadUrl(nodeId)} download>
          Download
        </a>
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- images

function ImageView({ nodeId, name }: { nodeId: string; name: string }) {
  const [zoom, setZoom] = useState(1);
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <CouldNotShow
        nodeId={nodeId}
        text="The picture is damaged or not a picture."
      />
    );
  return (
    <>
      <Zoombar
        zoom={zoom}
        onZoom={setZoom}
        label="Picture size"
        min={0.25}
        max={4}
      />
      <div
        className="viewer-scroll"
        tabIndex={0}
        aria-label={`Picture ${name}`}
      >
        {/* An <img> never runs scripts, even for an SVG file. */}
        <img
          className="viewer-image"
          src={api.viewUrl(nodeId)}
          alt={name}
          style={
            zoom === 1
              ? undefined
              : { width: `${zoom * 100}%`, maxWidth: "none" }
          }
          onError={() => setFailed(true)}
        />
      </div>
    </>
  );
}

function Zoombar({
  zoom,
  onZoom,
  label,
  min,
  max,
  extra,
}: {
  zoom: number;
  onZoom: (z: number) => void;
  label: string;
  min: number;
  max: number;
  extra?: React.ReactNode;
}) {
  const step = (f: number): void =>
    onZoom(Math.min(max, Math.max(min, Math.round(zoom * f * 100) / 100)));
  return (
    <div className="viewer-tools" role="group" aria-label={label}>
      <button
        type="button"
        aria-label="Zoom out"
        disabled={zoom <= min}
        onClick={() => step(1 / 1.25)}
      >
        −
      </button>
      <span aria-live="polite" className="viewer-zoom">
        {Math.round(zoom * 100)}%
      </span>
      <button
        type="button"
        aria-label="Zoom in"
        disabled={zoom >= max}
        onClick={() => step(1.25)}
      >
        +
      </button>
      <button type="button" onClick={() => onZoom(1)}>
        Fit
      </button>
      {extra}
    </div>
  );
}

// ---------------------------------------------------------------- text and tables

type TextState =
  | { state: "loading" }
  | { state: "error"; text: string }
  | { state: "ready"; text: string; truncated: boolean; total: number | null };

function TextView({
  nodeId,
  delimiter,
  onEnded,
}: {
  nodeId: string;
  delimiter: "," | "\t" | null;
  onEnded: () => void;
}) {
  const [data, setData] = useState<TextState>({ state: "loading" });
  const [table, setTable] = useState<Table | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        const res = await fetch(api.viewUrl(nodeId), {
          headers: { Range: `bytes=0-${TEXT_LIMIT - 1}` },
          credentials: "same-origin",
          signal: abort.signal,
        });
        if (res.status === 401) {
          onEnded();
          return;
        }
        if (!res.ok) {
          let code = "UNKNOWN";
          try {
            const body = (await res.json()) as { detail?: { code?: string } };
            code = body.detail?.code ?? code;
          } catch {
            /* no JSON body */
          }
          setData({ state: "error", text: describeError(code) });
          return;
        }
        const range = /\/(\d+)$/.exec(res.headers.get("Content-Range") ?? "");
        const total = range ? Number(range[1]) : null;
        const truncated = total !== null && total > TEXT_LIMIT;
        let text = new TextDecoder("utf-8").decode(await res.arrayBuffer());
        if (truncated) text = text.replace(/�$/, ""); // a character cut by the limit
        if (delimiter) setTable(parseDelimited(text, delimiter));
        setData({ state: "ready", text, truncated, total });
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") return;
        setData({ state: "error", text: describeError("UNKNOWN") });
      }
    })();
    return () => abort.abort();
  }, [nodeId, delimiter, onEnded]);

  if (data.state === "loading") return <p className="loading">Loading…</p>;
  if (data.state === "error")
    return <CouldNotShow nodeId={nodeId} text={data.text} />;
  return (
    <>
      {data.truncated && (
        <p className="viewer-banner">
          Showing the first {formatSize(TEXT_LIMIT)}
          {data.total !== null ? ` of ${formatSize(data.total)}` : ""}. Download
          the file to read the rest.
        </p>
      )}
      {table ? (
        <TableView table={table} />
      ) : (
        <pre className="viewer-text" tabIndex={0}>
          {data.text}
        </pre>
      )}
    </>
  );
}

function TableView({ table }: { table: Table }) {
  if (table.header.length === 0)
    return <p className="hint">This file is empty.</p>;
  return (
    <>
      <div className="viewer-scroll" tabIndex={0}>
        <table className="viewer-table">
          <thead>
            <tr>
              {table.header.map((h, i) => (
                <th key={i} scope="col">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.moreRows && (
        <p className="viewer-banner">
          Showing the first {table.rows.length.toLocaleString()} rows. Download
          the file to see the rest.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Office and ZIP (M10)

async function errorCode(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: { code?: string } };
    return body.detail?.code ?? "UNKNOWN";
  } catch {
    return "UNKNOWN";
  }
}

/** Shown when a Private or Confidential file needs the person's say-so before the server reads it
 * to make a preview. Nothing is sent to a cloud service. */
function AllowPreviews({ onAllowed }: { onAllowed: () => void }) {
  const [error, setError] = useState("");
  return (
    <section className="viewer-note" role="alert">
      <h2>Allow previews of protected files?</h2>
      <p>{describeError("LOCAL_PROCESSING_OPT_IN_REQUIRED")}</p>
      <p>You can turn this off again at any time under Preview settings.</p>
      {error && <p className="error">{error}</p>}
      <p>
        <button
          type="button"
          className="primary"
          onClick={() =>
            void api
              .setLocalProcessing(true)
              .then(onAllowed)
              .catch((err: unknown) =>
                setError(
                  describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
                ),
              )
          }
        >
          Allow on this server
        </button>
      </p>
    </section>
  );
}

type Probe =
  | { state: "loading" }
  | { state: "ready" }
  | { state: "optin" }
  | { state: "error"; code: string };

function OfficeView({
  nodeId,
  onEnded,
}: {
  nodeId: string;
  onEnded: () => void;
}) {
  const [probe, setProbe] = useState<Probe>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setProbe({ state: "loading" });
    (async () => {
      try {
        // The first request makes the PDF (it can take a while); later ones are served from the
        // server's cache. A one-byte range keeps this check cheap.
        const res = await fetch(api.previewUrl(nodeId), {
          headers: { Range: "bytes=0-0" },
          credentials: "same-origin",
          signal: abort.signal,
        });
        if (res.status === 401) return onEnded();
        if (res.ok) return setProbe({ state: "ready" });
        const code = await errorCode(res);
        setProbe(
          code === "LOCAL_PROCESSING_OPT_IN_REQUIRED"
            ? { state: "optin" }
            : { state: "error", code },
        );
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") return;
        setProbe({ state: "error", code: "UNKNOWN" });
      }
    })();
    return () => abort.abort();
  }, [nodeId, onEnded, attempt]);

  if (probe.state === "loading")
    return (
      <p className="loading" aria-live="polite">
        Preparing the preview… the first time can take up to a minute.
      </p>
    );
  if (probe.state === "optin")
    return <AllowPreviews onAllowed={() => setAttempt((n) => n + 1)} />;
  if (probe.state === "error") {
    if (probe.code === "CONVERTER_UNAVAILABLE")
      return (
        <Unsupported
          nodeId={nodeId}
          reason="Previews of Word, Excel and PowerPoint files need an optional add-on that is not running here."
        />
      );
    return <CouldNotShow nodeId={nodeId} text={describeError(probe.code)} />;
  }
  return (
    <>
      <p className="viewer-banner">
        Approximate preview. Fonts, spacing and some features can differ from
        the original. Download the file for the exact document.
      </p>
      <PdfView nodeId={nodeId} onEnded={onEnded} url={api.previewUrl(nodeId)} />
    </>
  );
}

type ArchiveState =
  | { state: "loading" }
  | { state: "optin" }
  | { state: "error"; code: string }
  | { state: "ready"; listing: ArchiveListing };

function ArchiveView({
  nodeId,
  onEnded,
}: {
  nodeId: string;
  onEnded: () => void;
}) {
  const [data, setData] = useState<ArchiveState>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setData({ state: "loading" });
    api
      .archive(nodeId)
      .then((listing) => live && setData({ state: "ready", listing }))
      .catch((err: unknown) => {
        if (!live) return;
        if (err instanceof ApiError && err.status === 401) return onEnded();
        const code = err instanceof ApiError ? err.code : "UNKNOWN";
        setData(
          code === "LOCAL_PROCESSING_OPT_IN_REQUIRED"
            ? { state: "optin" }
            : { state: "error", code },
        );
      });
    return () => {
      live = false;
    };
  }, [nodeId, onEnded, attempt]);

  if (data.state === "loading") return <p className="loading">Loading…</p>;
  if (data.state === "optin")
    return <AllowPreviews onAllowed={() => setAttempt((n) => n + 1)} />;
  if (data.state === "error")
    return <CouldNotShow nodeId={nodeId} text={describeError(data.code)} />;
  const { listing } = data;
  return (
    <>
      <p className="viewer-banner">
        This is a list of what is inside. Nothing has been opened or unpacked.
        {listing.suspicious_ratio &&
          " Some files in it expand to far more than their packed size; be careful opening it."}
        {listing.encrypted && " Some files in it are password protected."}
      </p>
      <div className="viewer-scroll" tabIndex={0}>
        <table className="viewer-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Size</th>
            </tr>
          </thead>
          <tbody>
            {listing.entries.map((e, i) => (
              <tr key={i}>
                <td>
                  {e.name}
                  {e.unsafe_name && " (unusual path, not safe to unpack)"}
                </td>
                <td>{e.folder ? "" : formatSize(e.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {listing.truncated && (
        <p className="viewer-banner">
          Showing the first {listing.entries.length.toLocaleString()} of{" "}
          {listing.count.toLocaleString()} entries.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------- PDF

type PdfJs = typeof import("pdfjs-dist");
type Doc = import("pdfjs-dist").PDFDocumentProxy;

// PDF.js is bundled with the app and loaded only when a PDF is opened. Its worker, fonts, CMaps
// and decoders are served from this origin (see vite.config.ts); scripting and eval stay off.
let pdfjsPromise: Promise<PdfJs> | null = null;
function loadPdfJs(): Promise<PdfJs> {
  pdfjsPromise ??= Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = worker.default;
    return lib;
  });
  return pdfjsPromise;
}

const PDF_ASSETS = "/pdfjs/";

function pdfMessage(err: unknown): string {
  const name = (err as { name?: string })?.name;
  const status = (err as { status?: number })?.status;
  if (name === "PasswordException")
    return "This PDF is protected with a password.";
  if (name === "InvalidPDFException")
    return "This PDF is damaged or incomplete.";
  if (name === "UnexpectedResponseException" && status === 403)
    return describeError("FORBIDDEN");
  if (
    name === "UnexpectedResponseException" &&
    (status === 415 || status === 422)
  )
    return "This does not look like a PDF.";
  return "This PDF could not be read.";
}

function PdfView({
  nodeId,
  onEnded,
  url,
}: {
  nodeId: string;
  onEnded: () => void;
  url?: string;
}) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(0);
  const [current, setCurrent] = useState(1);
  const [firstSize, setFirstSize] = useState<[number, number] | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    let loaded: Doc | null = null;
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      try {
        const pdfjs = await loadPdfJs();
        const opened = pdfjs.getDocument({
          url: url ?? api.viewUrl(nodeId),
          enableXfa: false,
          stopAtErrors: false,
          cMapUrl: `${PDF_ASSETS}cmaps/`,
          cMapPacked: true,
          standardFontDataUrl: `${PDF_ASSETS}standard_fonts/`,
          wasmUrl: `${PDF_ASSETS}wasm/`,
          iccUrl: `${PDF_ASSETS}iccs/`,
        });
        task = opened;
        loaded = await opened.promise;
        if (!live) {
          void opened.destroy();
          return;
        }
        const first = await loaded.getPage(1);
        const vp = first.getViewport({ scale: 1 });
        if (!live) return;
        setFirstSize([vp.width, vp.height]);
        setDoc(loaded);
      } catch (err) {
        if (!live) return;
        if (
          (err as { name?: string })?.name === "UnexpectedResponseException" &&
          (err as { status?: number }).status === 401
        )
          onEnded();
        else setError(pdfMessage(err));
      }
    })();
    return () => {
      live = false;
      void task?.destroy();
    };
  }, [nodeId, onEnded, url]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = (): void => setWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [doc]);

  if (error) return <CouldNotShow nodeId={nodeId} text={error} />;
  if (!doc || !firstSize) return <p className="loading">Loading…</p>;

  // Fit to the width of the window (with a little margin), then apply the zoom.
  const fit = width > 0 ? Math.max(0.2, (width - 24) / firstSize[0]) : 1;
  const scale = fit * zoom;
  const pages = Array.from({ length: doc.numPages }, (_, i) => i + 1);

  return (
    <>
      <Zoombar
        zoom={zoom}
        onZoom={setZoom}
        label="Page size"
        min={0.5}
        max={4}
        extra={
          <span className="viewer-page" aria-live="polite">
            Page {current} of {doc.numPages}
          </span>
        }
      />
      <div
        className="viewer-scroll viewer-pdf"
        ref={scroller}
        tabIndex={0}
        aria-label="Document pages"
      >
        {pages.map((n) => (
          <PdfPage
            key={n}
            doc={doc}
            number={n}
            scale={scale}
            placeholder={[firstSize[0] * scale, firstSize[1] * scale]}
            root={scroller}
            onCurrent={setCurrent}
          />
        ))}
      </div>
    </>
  );
}

function PdfPage({
  doc,
  number,
  scale,
  placeholder,
  root,
  onCurrent,
}: {
  doc: Doc;
  number: number;
  scale: number;
  placeholder: [number, number];
  root: React.RefObject<HTMLDivElement | null>;
  onCurrent: (n: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);
  const [size, setSize] = useState<[number, number] | null>(null);

  // Draw a page only when it is close to the visible area; report the page in the middle.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const nearObs = new IntersectionObserver(
      ([entry]) => setNear(entry?.isIntersecting ?? false),
      { root: root.current, rootMargin: "100% 0px" },
    );
    const midObs = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) onCurrent(number);
      },
      { root: root.current, rootMargin: "-50% 0px -50% 0px" },
    );
    nearObs.observe(el);
    midObs.observe(el);
    return () => {
      nearObs.disconnect();
      midObs.disconnect();
    };
  }, [root, number, onCurrent]);

  useEffect(() => {
    if (!near) {
      // Free the memory of a page that scrolled far away.
      const c = canvas.current;
      if (c) {
        c.width = 0;
        c.height = 0;
      }
      return;
    }
    let live = true;
    let render: { cancel: () => void; promise: Promise<unknown> } | null = null;
    (async () => {
      try {
        const page = await doc.getPage(number);
        const c = canvas.current;
        if (!live || !c) return;
        const vp = page.getViewport({ scale });
        const ratio = window.devicePixelRatio || 1;
        c.width = Math.floor(vp.width * ratio);
        c.height = Math.floor(vp.height * ratio);
        setSize([vp.width, vp.height]);
        render = page.render({
          canvas: c,
          viewport: vp,
          transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
        });
        await render.promise;
      } catch {
        /* a cancelled render, or one bad page: the others still work */
      }
    })();
    return () => {
      live = false;
      render?.cancel();
    };
  }, [doc, number, scale, near]);

  const [w, h] = size ?? placeholder;
  return (
    <div
      ref={box}
      className="viewer-sheet"
      style={{ width: w, height: h }}
      data-page={number}
    >
      <canvas
        ref={canvas}
        role="img"
        aria-label={`Page ${number}`}
        style={{ width: w, height: h }}
      />
    </div>
  );
}
