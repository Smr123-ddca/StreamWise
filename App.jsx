import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertCircle, ArrowUpRight, Check, ChevronRight, CircleHelp, CloudUpload, FileVideo, Gauge, HardDrive, Link2, Loader2, Play, Radio, RefreshCw, Settings2, ShieldCheck, Wifi } from "lucide-react";
import Sidebar from "./components/Sidebar";
import VideoPlayer from "./components/VideoPlayer";
import { useObjectUrl } from "./hooks/useObjectUrl";
import { config } from "./config";
import { validateVideoFile, isHlsUrl } from "./services/videoService";
import { createExperiment } from "./services/experimentService";

const initialNetwork = { name: "Stable broadband", bandwidthMbps: 8, latencyMs: 40, packetLossPct: 0 };
const pageTitles = {
  studio: ["Experiment studio", "Set up a controlled streaming comparison."],
  playback: ["Playback lab", "Preview the selected source and inspect playback."],
  intelligence: ["Segment intelligence", "Connect the controller's per-segment decisions here."],
  analytics: ["Analytics", "Only measured experiment data belongs in this view."],
  archive: ["Experiment archive", "Your saved experiment runs."]
};

function SectionHeading({ eyebrow, title, description, action }) {
  return <div className="mb-6 flex flex-wrap items-end justify-between gap-3"><div><p className="mono mb-2 text-[10px] font-medium uppercase tracking-[.2em] text-imdb-gold">{eyebrow}</p><h2 className="text-2xl font-bold tracking-tight text-imdb-white sm:text-[28px]">{title}</h2><p className="mt-1.5 max-w-2xl text-[13px] leading-6 text-imdb-silver">{description}</p></div>{action}</div>;
}
function Panel({ children, className = "" }) { return <section className={`panel ${className}`}>{children}</section>; }
function FieldLabel({ children }) { return <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-imdb-silver">{children}</label>; }

export default function App() {
  const [active, setActive] = useState("studio");
  const [file, setFile] = useState(null);
  const [sourceMode, setSourceMode] = useState("file");
  const [hlsInput, setHlsInput] = useState("");
  const [hlsUrl, setHlsUrl] = useState("");
  const [fileError, setFileError] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [notice, setNotice] = useState("");
  const [network, setNetwork] = useState(initialNetwork);
  const [algorithm, setAlgorithm] = useState("streamwise");
  const [experimentName, setExperimentName] = useState("Lecture video comparison");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState([]);
  const inputRef = useRef(null);
  const objectUrl = useObjectUrl(file);

  const source = useMemo(() => {
    if (sourceMode === "hls" && hlsUrl) return { kind: "hls", url: hlsUrl, name: hlsUrl };
    if (file && objectUrl) return { kind: "file", url: objectUrl, name: file.name };
    return null;
  }, [sourceMode, hlsUrl, file, objectUrl]);

  useEffect(() => {
    return () => { setNotice(""); };
  }, []);

  const selectFile = (nextFile) => {
    setFileError("");
    setExperimentError("");
    if (!nextFile) return;
    try {
      validateVideoFile(nextFile);
      setFile(nextFile);
      setSourceMode("file");
      setHlsUrl("");
      setNotice("Video selected. The preview uses this exact local file.");
    } catch (err) {
      setFileError(err.message);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const applyHls = () => {
    const trimmed = hlsInput.trim();
    if (!trimmed || !/^https?:\/\//i.test(trimmed) || !isHlsUrl(trimmed)) {
      setFileError("Enter a valid http(s) HLS .m3u8 manifest URL.");
      return;
    }
    setFileError("");
    setHlsUrl(trimmed);
    setSourceMode("hls");
    setNotice("HLS source selected. Local file selection has not been changed.");
  };

  const startExperiment = async () => {
    setExperimentError("");
    if (!source) {
      setExperimentError("Select a local video or an HLS manifest before starting.");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        name: experimentName.trim() || "Untitled experiment",
        source: source.kind === "hls" ? { type: "hls", manifest_url: source.url } : {
          type: "local_preview",
          file_name: file?.name,
          file_size_bytes: file?.size,
          mime_type: file?.type || "application/octet-stream"
        },
        algorithm,
        network_profile: network
      };
      if (config.useMockApi) {
        const run = { id: `LOCAL-${Date.now()}`, name: payload.name, algorithm, sourceName: source.name, createdAt: new Date().toISOString(), status: "Prepared locally", isMock: true };
        setRuns((current) => [run, ...current]);
        setNotice("Experiment configuration prepared locally. Connect the backend API to execute and measure a real run.");
      } else {
        const result = await createExperiment(payload);
        setRuns((current) => [{ ...result, name: result.name || payload.name, algorithm, sourceName: source.name, createdAt: result.created_at || new Date().toISOString(), status: result.status || "Submitted", isMock: false }, ...current]);
        setNotice("Experiment request sent to the backend.");
      }
    } catch (err) {
      setExperimentError(err.message || "Could not start the experiment.");
    } finally {
      setBusy(false);
    }
  };

  const title = pageTitles[active];
  return (
    <div className="min-h-screen bg-imdb-black text-imdb-white lg:flex">
      <Sidebar active={active} onChange={setActive} />
      <main className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex min-h-[68px] items-center justify-between border-b border-imdb-line bg-[#161616]/95 px-5 backdrop-blur sm:px-8">
          <div><p className="mono text-[9px] uppercase tracking-[.18em] text-imdb-silver">STREAMING RESEARCH WORKSPACE</p><p className="mt-1 text-sm font-semibold">{title[0]}</p></div>
          <div className="flex items-center gap-2 rounded-full border border-imdb-line bg-[#202020] px-3 py-1.5"><span className={`h-2 w-2 rounded-full ${config.useMockApi ? "bg-imdb-gold" : "bg-emerald-400"}`} /><span className="mono text-[10px] text-imdb-silver">{config.useMockApi ? "LOCAL MODE" : "API MODE"}</span></div>
        </header>

        <div className="mx-auto max-w-[1520px] px-5 py-6 sm:px-8 sm:py-8">
          <SectionHeading eyebrow="WORKSPACE" title={title[0]} description={title[1]} action={<div className="mono flex items-center gap-2 text-[10px] text-imdb-silver"><ShieldCheck size={14} className="text-imdb-gold" /> </div>} />

          {active === "studio" && (
            <div className="grid items-start gap-5 2xl:grid-cols-[minmax(300px,.82fr)_minmax(0,1.18fr)]">
              <div className="space-y-5">
                <Panel className="overflow-hidden">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-imdb-line px-5 py-4"><div><h3 className="text-sm font-bold">Video source</h3><p className="mt-1 text-xs text-imdb-silver">Upload a local file or choose an HLS manifest.</p></div><span className="mono rounded-md bg-[#302b18] px-2.5 py-1 text-[10px] text-imdb-gold">SOURCE 01</span></div>
                  <div className="p-4 sm:p-5">
                    <div className="mb-4 flex gap-2">
                      <button onClick={() => { setSourceMode("file"); setFileError(""); }} className={`rounded-lg px-3 py-2 text-xs font-semibold ${sourceMode === "file" ? "bg-imdb-gold text-imdb-black" : "bg-[#303030] text-imdb-silver hover:text-white"}`}><FileVideo size={14} className="mr-2 inline" />Local video</button>
                      <button onClick={() => { setSourceMode("hls"); setFileError(""); }} className={`rounded-lg px-3 py-2 text-xs font-semibold ${sourceMode === "hls" ? "bg-imdb-gold text-imdb-black" : "bg-[#303030] text-imdb-silver hover:text-white"}`}><Link2 size={14} className="mr-2 inline" />HLS URL</button>
                    </div>
                    {sourceMode === "file" ? (
                      <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); selectFile(e.dataTransfer.files?.[0]); }} className="rounded-lg border border-dashed border-[#555] bg-[#191919] px-4 py-6 text-center transition-colors hover:border-imdb-gold/70">
                        <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-[#302b18] text-imdb-gold"><CloudUpload size={21} /></div>
                        <p className="text-sm font-semibold">Drop your video here</p><p className="mt-1 text-xs text-imdb-silver">MP4, WebM, MOV or OGG · up to {config.maxUploadMb} MB</p>
                        <button onClick={() => inputRef.current?.click()} className="mt-4 rounded-lg bg-imdb-gold px-4 py-2.5 text-xs font-bold text-imdb-black hover:bg-[#ffda45]">Choose video</button>
                        <input ref={inputRef} type="file" accept="video/mp4,video/webm,video/quicktime,video/ogg,.mp4,.webm,.mov,.ogv,.ogg" className="hidden" onChange={(e) => selectFile(e.target.files?.[0])} />
                      </div>
                    ) : (
                      <div className="rounded-lg border border-imdb-line bg-[#191919] p-4">
                        <FieldLabel>HLS manifest URL (.m3u8)</FieldLabel>
                        <div className="flex flex-col gap-2 sm:flex-row"><input value={hlsInput} onChange={(e) => setHlsInput(e.target.value)} placeholder="https://example.com/stream/master.m3u8" className="min-w-0 flex-1 rounded-lg border border-imdb-line bg-[#111] px-3 py-3 text-xs text-white placeholder:text-[#666]" /><button onClick={applyHls} className="rounded-lg bg-imdb-gold px-4 py-3 text-xs font-bold text-imdb-black">Use HLS URL</button></div>
                        <p className="mt-2 text-[11px] text-imdb-silver">The HLS source is kept separate from your uploaded local file.</p>
                      </div>
                    )}
                    {fileError && <p role="alert" className="mt-3 flex items-center gap-2 text-xs text-red-300"><AlertCircle size={14} />{fileError}</p>}
                    {file && sourceMode === "file" && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-imdb-line bg-[#1a1a1a] p-3"><div className="flex min-w-0 items-center gap-3"><FileVideo size={18} className="shrink-0 text-imdb-gold" /><div className="min-w-0"><p className="truncate text-xs font-semibold">{file.name}</p><p className="mono mt-1 text-[10px] text-imdb-silver">{(file.size / 1024 / 1024).toFixed(1)} MB · {file.type || "video file"}</p></div></div><button onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ""; setNotice("Local video removed."); }} className="text-xs text-imdb-silver hover:text-red-300">Remove</button></div>}
                    {source && <div className="mt-4 overflow-hidden rounded-lg border border-imdb-line"><VideoPlayer source={source} title={source.name} /><div className="flex flex-wrap items-center justify-between gap-2 bg-[#1b1b1b] px-3 py-2.5"><span className="flex min-w-0 items-center gap-2 text-xs text-imdb-white"><span className="h-2 w-2 rounded-full bg-emerald-400" /> <span className="truncate">{source.name}</span></span><span className="mono text-[9px] uppercase text-imdb-silver">{source.kind === "hls" ? "HLS SOURCE" : "LOCAL SOURCE"}</span></div></div>}
                    <p className="mt-3 flex items-start gap-2 text-[11px] leading-5 text-imdb-silver"><CircleHelp size={14} className="mt-0.5 shrink-0" />Local preview uses a browser object URL. It does not upload the file to your server. Backend upload can be connected through <code className="text-imdb-gold">videoService.js</code>.</p>
                  </div>
                </Panel>

                <Panel className="p-5">
                  <div className="mb-4 flex items-center gap-2"><Settings2 size={17} className="text-imdb-gold" /><h3 className="text-sm font-bold">Experiment configuration</h3></div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="sm:col-span-2"><FieldLabel>Experiment name</FieldLabel><input value={experimentName} onChange={(e) => setExperimentName(e.target.value)} className="w-full rounded-lg border border-imdb-line bg-[#171717] px-3 py-3 text-sm" /></div>
                    <div><FieldLabel>ABR algorithm</FieldLabel><select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)} className="w-full rounded-lg border border-imdb-line bg-[#171717] px-3 py-3 text-sm"><option value="streamwise">StreamWise controller</option><option value="conventional">Conventional ABR</option></select></div>
                    <div><FieldLabel>Playback profile</FieldLabel><div className="flex h-[43px] items-center gap-2 rounded-lg border border-imdb-line bg-[#171717] px-3 text-xs text-imdb-silver"><Activity size={15} className="text-imdb-blue" />Same source for both modes</div></div>
                  </div>
                </Panel>
              </div>

              <div className="space-y-5">
                <Panel className="p-5">
                  <div className="mb-4 flex items-center gap-2"><Wifi size={17} className="text-imdb-blue" /><h3 className="text-sm font-bold">Network profile</h3></div>
                  <div className="mb-4"><FieldLabel>Profile name</FieldLabel><input value={network.name} onChange={(e) => setNetwork({ ...network, name: e.target.value })} className="w-full rounded-lg border border-imdb-line bg-[#171717] px-3 py-2.5 text-xs" /></div>
                  <div className="space-y-5">
                    <div><div className="mb-2 flex justify-between"><FieldLabel>Bandwidth</FieldLabel><span className="mono text-xs text-imdb-blue">{network.bandwidthMbps} Mbps</span></div><input aria-label="Bandwidth Mbps" type="range" min="0.5" max="25" step="0.5" value={network.bandwidthMbps} onChange={(e) => setNetwork({ ...network, bandwidthMbps: Number(e.target.value) })} className="range-gold w-full" /></div>
                    <div><div className="mb-2 flex justify-between"><FieldLabel>Latency</FieldLabel><span className="mono text-xs text-imdb-blue">{network.latencyMs} ms</span></div><input aria-label="Latency milliseconds" type="range" min="0" max="500" step="10" value={network.latencyMs} onChange={(e) => setNetwork({ ...network, latencyMs: Number(e.target.value) })} className="range-gold w-full" /></div>
                    <div><div className="mb-2 flex justify-between"><FieldLabel>Packet loss</FieldLabel><span className="mono text-xs text-imdb-blue">{network.packetLossPct}%</span></div><input aria-label="Packet loss percentage" type="range" min="0" max="10" step="0.5" value={network.packetLossPct} onChange={(e) => setNetwork({ ...network, packetLossPct: Number(e.target.value) })} className="range-gold w-full" /></div>
                  </div>
                  <div className="mt-5 rounded-lg border border-[#25434b] bg-[#10232a] p-3"><p className="flex items-center gap-2 text-xs font-semibold text-imdb-blue"><Radio size={14} />Network simulation note</p><p className="mt-1 text-[11px] leading-5 text-[#a8c3cb]">These values are sent as experiment configuration. Real throttling requires backend/proxy support for manifest and segment requests.</p></div>
                </Panel>

                <Panel className="p-5">
                  <h3 className="text-sm font-bold">Ready to run?</h3>
                  <div className="mt-3 space-y-2 text-xs text-imdb-silver">
                    <p className="flex items-center gap-2"><Check size={14} className="text-imdb-gold" /> Source selection is preserved</p>
                    <p className="flex items-center gap-2"><Check size={14} className="text-imdb-gold" /> ABR mode is explicit</p>
                    <p className="flex items-center gap-2"><Check size={14} className="text-imdb-gold" /> Network profile is attached</p>
                  </div>
                  {experimentError && <p role="alert" className="mt-4 flex items-center gap-2 text-xs text-red-300"><AlertCircle size={14} />{experimentError}</p>}
                  <button onClick={startExperiment} disabled={busy || !source} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-imdb-gold px-4 py-3.5 text-sm font-extrabold text-imdb-black transition hover:bg-[#ffda45] disabled:cursor-not-allowed disabled:opacity-40">{busy ? <Loader2 size={17} className="animate-spin" /> : <Play size={16} fill="currentColor" />}Start experiment setup</button>
                  <p className="mt-2 text-center text-[10px] text-imdb-silver">{config.useMockApi ? "Local preparation only · no research metrics generated" : "Submits configuration to your backend API"}</p>
                </Panel>
              </div>
            </div>
          )}

          {active === "playback" && <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]"><Panel className="overflow-hidden"><div className="border-b border-imdb-line px-5 py-4"><h3 className="text-sm font-bold">Playback monitor</h3><p className="mt-1 text-xs text-imdb-silver">The currently selected source is used without substitution.</p></div><VideoPlayer source={source} title={source?.name} /><div className="flex flex-wrap items-center justify-between gap-2 p-4"><span className="truncate text-xs text-imdb-silver">{source?.name || "No source selected"}</span><button onClick={() => setActive("studio")} className="text-xs font-semibold text-imdb-gold">Change source <ChevronRight className="ml-1 inline" size={14} /></button></div></Panel><Panel className="p-5"><h3 className="text-sm font-bold">Source details</h3><div className="mt-4 space-y-4 text-xs"><div><p className="text-imdb-silver">Source type</p><p className="mt-1 font-semibold">{source?.kind === "hls" ? "HLS manifest" : source ? "Local file" : "Not selected"}</p></div><div><p className="text-imdb-silver">Selected source</p><p className="mt-1 break-all font-semibold">{source?.name || "—"}</p></div><div><p className="text-imdb-silver">Network profile</p><p className="mt-1 font-semibold">{network.name}</p></div></div></Panel></div>}

          {active === "intelligence" && <Panel className="p-6"><div className="mx-auto max-w-xl py-10 text-center"><Layers3 size={30} className="mx-auto text-imdb-gold" /><h3 className="mt-4 text-lg font-bold">Controller integration point</h3><p className="mt-2 text-sm leading-6 text-imdb-silver">Connect your teammate's segment-importance and rendition-decision API here. The frontend intentionally does not invent segment scores or bitrate decisions.</p><p className="mono mt-5 inline-block rounded-lg border border-imdb-line bg-[#171717] px-3 py-2 text-[10px] text-imdb-blue">GET /api/decisions?experiment_id=...</p></div></Panel>}

          {active === "analytics" && <Panel className="p-6"><div className="mx-auto max-w-xl py-10 text-center"><Gauge size={30} className="mx-auto text-imdb-gold" /><h3 className="mt-4 text-lg font-bold">No measured results yet</h3><p className="mt-2 text-sm leading-6 text-imdb-silver">Analytics will populate after the backend returns real player events, transferred bytes, buffer events and segment-level decisions. Local preview values are not presented as research results.</p></div></Panel>}

          {active === "archive" && <Panel className="overflow-hidden"><div className="flex items-center justify-between border-b border-imdb-line px-5 py-4"><h3 className="text-sm font-bold">Experiment runs</h3><span className="mono text-[10px] text-imdb-silver">{runs.length} LOCAL SESSION RUNS</span></div>{runs.length ? <div className="divide-y divide-imdb-line">{runs.map((run) => <div key={run.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"><div className="min-w-0"><p className="truncate text-sm font-semibold">{run.name}</p><p className="mono mt-1 truncate text-[10px] text-imdb-silver">{run.id} · {run.sourceName} · {run.algorithm}</p></div><span className="rounded-md bg-[#302b18] px-2.5 py-1 text-[10px] text-imdb-gold">{run.status}</span></div>)}</div> : <div className="py-14 text-center"><Archive size={26} className="mx-auto text-imdb-silver" /><p className="mt-3 text-sm font-semibold">No experiments in this session</p><p className="mt-1 text-xs text-imdb-silver">Prepared runs will appear here.</p></div>}</Panel>}

          {notice && <div role="status" className="mt-5 flex items-start gap-2 rounded-lg border border-[#51451c] bg-[#252114] px-4 py-3 text-xs leading-5 text-[#e9d88e]"><Check size={15} className="mt-0.5 shrink-0" />{notice}<button onClick={() => setNotice("")} className="ml-auto text-[#b9a65b] hover:text-white" aria-label="Dismiss notice">×</button></div>}
          
        </div>
      </main>
    </div>
  );
}