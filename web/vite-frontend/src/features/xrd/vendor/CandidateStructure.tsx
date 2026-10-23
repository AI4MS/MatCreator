import { useEffect, useRef, useState } from "react";
import type { PluginViewProps } from "../sdk";



type Structure = {
  cod_id: string;
  filename: string;
  source_base64: string;
  source_url: string;
  local_path: string;
};

/** A library reference code alone is not necessarily a COD identifier. */
export function codCandidateId(metadata: Record<string, string>): string | undefined {
  const id = metadata.reference_code?.replace(/^COD\s*/i, "").trim();
  if (!id || !/^\d{7}$/.test(id)) return;
  try {
    const source = new URL(metadata.url);
    if (source.protocol === "https:" && source.hostname === "www.crystallography.net"
      && source.pathname === `/cod/${id}.html`) return id;
  } catch { /* User-provided references need not have a source URL. */ }
}

export { McStructureCanvas as StructureCanvas } from '../McStructureCanvas';

/** Keyed by the selected COD ID so a late response never opens a different candidate. */
export function CandidateStructure({ codId, host }: { codId: string; host: PluginViewProps["host"] }) {
  const [structure, setStructure] = useState<Structure>();
  const [download, setDownload] = useState<{ structure: Structure; url: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  const downloadLink = useRef<HTMLAnchorElement>(null);
  const downloadPending = useRef(false);
  useEffect(() => { if (download && downloadPending.current) { downloadPending.current=false; downloadLink.current?.click(); } }, [download]);
  const acceptDocument = (value: unknown) => {
    const saved = (value as { structure?: Structure | null } | null)?.structure;
    setStructure(saved?.cod_id === codId ? saved : undefined);
  };
  useEffect(() => {
    if (!structure) return;
    try {
      const bytes = Uint8Array.from(atob(structure.source_base64), character => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "chemical/x-cif" }));
      setDownload({ structure, url });
      return () => URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [structure]);
  useEffect(() => {
    const current = ++request.current;
    void host.readDocument().then(document => {
      if (request.current === current) acceptDocument(document.value);
    }).catch(reason => {
      if (request.current === current) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { ++request.current; };
  }, [host, codId]);
  const fetchStructure = async (open = true) => {
    const current = ++request.current;
    setBusy(true); setError("");
    try {
      const document = await host.readDocument();
      if (request.current !== current) return;
      const existing = (document.value as { structure?: Structure }).structure;
      const saved = existing?.cod_id === codId ? document : await host.documentAction("fetch_cod", { cod_id: codId }, document.revision);
      if (request.current !== current) return;
      const value = (saved.value as { structure?: Structure | null }).structure;
      if (value?.cod_id !== codId) throw new Error("返回的结构条目与所选候选不一致，请重试。");
      downloadPending.current = !open;
      acceptDocument(saved.value);
      if (!open) return;
      if (host.openLinkedCanvas) {
        await host.openLinkedCanvas('xrd.structure-canvas', 'XRD · 结构画布', value);
        return;
      }
      if (!host.openInputNode) throw new Error("请刷新页面以启用独立结构画布");
      await host.openInputNode("xrd.cif", `COD ${codId} · 候选结构`, { filename: value.filename, source_base64: value.source_base64 }, "xrd.input");
    } catch (reason) {
      if (request.current === current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (request.current === current) setBusy(false);
    }
  };
  return <section className="xrd-candidate-structure" aria-label={`候选结构 COD ${codId}`}>
    <div className="xrd-structure-toolbar">
      <a href={`https://www.crystallography.net/cod/${codId}.html`} target="_blank" rel="noreferrer" aria-label="查看 COD 结构与文献" title="查看 COD 结构与文献"><ActionIcon kind="search"/></a>
      {structure && download?.structure === structure
        ? <a ref={downloadLink} href={download.url} download={structure.filename} aria-label={`下载 COD ${codId} CIF`} title="下载 CIF"><ActionIcon kind="download"/></a>
        : <button type="button" disabled={busy} aria-label={`下载 COD ${codId} CIF`} title="下载 CIF" onClick={()=>void fetchStructure(false)}><ActionIcon kind="download"/></button>}
      <button type="button" disabled={busy} aria-label="打开结构画布" title="打开结构画布并连接" onClick={() => void fetchStructure()}><ActionIcon kind="canvas"/></button>
    </div>
    {busy && <small role="status">正在从 COD 获取 CIF 并保存到本地…</small>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function ActionIcon({kind}:{kind:'search'|'download'|'canvas'}) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind==='search'?<><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>:kind==='download'?<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>:<><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m7 15 4-7 6 8-10-1Z"/><circle cx="11" cy="8" r="1"/></>}</svg>;
}
