/* SlipVideo.jsx — "▶ Video" of an AUTO breakdown slip (camera clip cut by the
 * breakdown clipper), same behaviour as the 9965 slip (2026-10-17, operator:
 * "9965 jaisa slip ke saath Video + ID").
 *   • nothing is fetched until the button is pressed (this component does not
 *     exist before that);
 *   • then only `/api/bd-clip/<src>/<id>/info` (small JSON: clips + signed links);
 *   • the video plays by itself, muted (the clips have no sound, and muted video
 *     is allowed to autoplay); the browser pulls it in pieces (HTTP Range), so a
 *     long clip never downloads in one go;
 *   • a slip filmed by two cameras gets Camera 1 / Camera 2 buttons;
 *   • closing drops the video source so the buffer is released at once.
 * Rendered in a portal on <body> so it sits above any slip modal; clicks are
 * stopped here so closing the video never closes the slip under it.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const ERRORS = {
  1: "The video was stopped.",
  2: "Network problem while loading the video. Check the connection and try again.",
  3: "This device could not decode the video.",
  4: "This video cannot be played on this device.",
};

export default function SlipVideo({ src, slipId, token, title, onClose, machineNo }) {
  const [info, setInfo] = useState(null);          // null = checking
  const [cam, setCam] = useState(0);
  const [state, setState] = useState("load");      // load | play | paused | error
  const [err, setErr] = useState("");
  const vref = useRef(null);

  useEffect(() => {
    let alive = true;
    const s = /tool/i.test(src || "") ? "toolroom" : "maintenance";
    // 2026-10-23 — the machine picked on the form lets a pending slip show a
    // preview from the camera recording before it is submitted.
    const q = machineNo ? `?machine_no=${encodeURIComponent(machineNo)}` : "";
    fetch(`/api/bd-clip/${s}/${slipId}/info${q}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (r) => (r.ok ? r.json()
        : { available: false, reason: r.status === 403 ? "This line is not assigned to you."
                                                       : `Could not check the video (HTTP ${r.status}).` }))
      .catch(() => ({ available: false, reason: "Could not reach the server." }))
      .then((d) => { if (alive) setInfo(d); });
    return () => { alive = false; };
  }, [src, slipId, token, machineNo]);

  // release the buffer / stop the download as soon as the player goes away
  useLayoutEffect(() => () => {
    const v = vref.current;
    if (!v) return;
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch { /* already gone */ }
  }, []);

  const play = () => {
    const v = vref.current;
    if (!v) return;
    const p = v.play();
    if (p && p.catch) p.catch(() => setState((s) => (s === "error" ? s : "paused")));
  };

  const clips = (info && info.clips) || [];
  const clip = clips[cam] || null;
  const close = (e) => { if (e) e.stopPropagation(); onClose(); };
  const overlay = { position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
                    pointerEvents: "none", color: "#e2e8f0", fontSize: 13.5, fontWeight: 700, textAlign: "center", padding: 20 };

  return createPortal(
    <div onClick={close}
         style={{ position: "fixed", inset: 0, zIndex: 20000, background: "rgba(2,6,23,.82)", display: "flex",
                  alignItems: "center", justifyContent: "center", padding: 14 }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ width: "min(960px, 100%)", background: "#0f172a", borderRadius: 14, overflow: "hidden",
                    boxShadow: "0 24px 70px rgba(0,0,0,.6)", fontFamily: "'Barlow',system-ui,sans-serif" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", color: "#e2e8f0", flexWrap: "wrap" }}>
          <span style={{ fontSize: 14.5, fontWeight: 800 }}>▶ Breakdown Video</span>
          <span style={{ fontSize: 12, fontWeight: 600, color: "#94a3b8", flex: 1, minWidth: 0, overflow: "hidden",
                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
          {clips.length > 1 && clips.map((c, i) => (
            <button key={c.url} onClick={() => { setCam(i); setState("load"); setErr(""); }}
                    style={{ border: "none", borderRadius: 7, padding: "4px 10px", fontSize: 12, fontWeight: 800,
                             cursor: "pointer", fontFamily: "inherit",
                             background: i === cam ? "#2563eb" : "#1e293b", color: "#fff" }}>{c.label}</button>
          ))}
          {clip && clip.size_mb != null && <span style={{ fontSize: 11.5, fontWeight: 700, color: "#94a3b8", whiteSpace: "nowrap" }}>{clip.size_mb} MB</span>}
          <button onClick={close} title="Close"
                  style={{ border: "none", background: "#1e293b", color: "#e2e8f0", borderRadius: 8, width: 32, height: 32,
                           fontSize: 18, cursor: "pointer", fontFamily: "inherit" }}>×</button>
        </div>
        <div style={{ position: "relative", background: "#000", minHeight: 220, display: "flex", alignItems: "center", justifyContent: "center" }}>
          {info === null ? (
            <span style={{ color: "#94a3b8", fontSize: 13, fontWeight: 600 }}>Checking…</span>
          ) : clip ? (
            <>
              <video ref={vref} key={clip.url} src={clip.url} controls playsInline autoPlay muted preload="auto"
                     onLoadedMetadata={play}
                     onPlaying={() => setState("play")}
                     onWaiting={() => setState((s) => (s === "play" ? "load" : s))}
                     onCanPlay={() => setState((s) => (s === "load" && !vref.current?.paused ? "play" : s))}
                     onError={() => { setErr(ERRORS[vref.current?.error?.code] || "The video could not be loaded."); setState("error"); }}
                     style={{ width: "100%", maxHeight: "76vh", display: "block", background: "#000" }} />
              {state === "load" && <div style={overlay}>{info.preview ? "Preparing the video… (up to 15 s)" : "Loading video…"}</div>}
              {state === "paused" && (
                <div style={{ ...overlay, pointerEvents: "auto" }}>
                  <button onClick={play} title="Play"
                          style={{ border: "none", background: "rgba(37,99,235,.95)", color: "#fff", borderRadius: 99, width: 76, height: 76,
                                   fontSize: 30, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 8px 30px rgba(0,0,0,.5)" }}>▶</button>
                </div>
              )}
              {state === "error" && (
                <div style={{ ...overlay, background: "rgba(0,0,0,.75)", flexDirection: "column", gap: 6 }}>
                  <div style={{ fontSize: 26 }}>⚠</div>{err}
                </div>
              )}
            </>
          ) : (
            <div style={{ color: "#cbd5e1", fontSize: 14, fontWeight: 600, textAlign: "center", padding: "36px 20px" }}>
              <div style={{ fontSize: 30, marginBottom: 8 }}>🎞</div>
              {info.reason || "No video for this slip."}
            </div>
          )}
        </div>
        {info && info.preview && info.note && (
          <div style={{ padding: "8px 14px", fontSize: 12, fontWeight: 600, color: "#fbbf24", background: "#111827" }}>
            {info.note} The saved clip replaces this after the slip is submitted.
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
