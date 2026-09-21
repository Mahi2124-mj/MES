/**
 * Always-visible progress + time for a <video>.
 *
 * 2026-08-22 — operator: "saari video me jo progress bar hai wo hide na ho,
 * hamesha unhide hi rahni chahiye taaki video ki progress aur time dikhta rahe".
 *
 * The browser's own control strip fades out a few seconds into playback and
 * there is no cross-browser way to pin it (Chromium exposes
 * ::-webkit-media-controls-panel, Firefox exposes nothing), so this replaces it
 * with a strip that never fades.
 *
 * 2026-08-22 (later) — `controls` was dropped from the players because the
 * native strip sat underneath this one and the operator saw two bars.  So this
 * component now owns play/pause, mute and fullscreen too; nothing the native
 * controls offered was lost.
 *
 * Reads straight off the element on a 200 ms tick rather than relying only on
 * `timeupdate`: the same component is used inside modals that mount the video
 * conditionally (and one that remounts it on every dot click), so polling means
 * there is no listener-attach race to get wrong. Events are wired too so the
 * bar still snaps immediately on seek/load.
 *
 * Click or drag anywhere on the bar to seek.
 */
import { useEffect, useRef, useState } from "react";

const fmt = (s) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};

export default function VideoProgressBar({ videoRef, compact = false }) {
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const [paused, setPaused] = useState(true);
  const [muted, setMuted] = useState(true);
  const barRef = useRef(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    let raf = 0;
    const read = () => {
      const v = videoRef?.current;
      if (!v) return;
      if (!draggingRef.current) setCur(v.currentTime || 0);
      const d = v.duration;
      setDur(isFinite(d) && d > 0 ? d : 0);
      setPaused(!!v.paused);
      setMuted(!!v.muted);
    };
    const id = setInterval(read, 200);
    const v = videoRef?.current;
    const evts = ["timeupdate", "loadedmetadata", "durationchange", "seeked",
                  "progress", "play", "pause", "volumechange", "ended"];
    if (v) evts.forEach((e) => v.addEventListener(e, read));
    read();
    return () => {
      clearInterval(id);
      cancelAnimationFrame(raf);
      if (v) evts.forEach((e) => v.removeEventListener(e, read));
    };
  }, [videoRef]);

  const seekTo = (clientX) => {
    const v = videoRef?.current;
    const el = barRef.current;
    if (!v || !el || !dur) return;
    const r = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    try { v.currentTime = frac * dur; } catch { /* not seekable yet */ }
    setCur(frac * dur);
  };

  const onDown = (e) => {
    draggingRef.current = true;
    seekTo(e.clientX);
    const move = (ev) => seekTo(ev.clientX);
    const up = () => {
      draggingRef.current = false;
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const togglePlay = () => {
    const v = videoRef?.current;
    if (!v) return;
    // A clip that has run to the end restarts instead of sitting on the last
    // frame doing nothing when the operator hits play again.
    if (v.ended || (dur && v.currentTime >= dur - 0.05)) v.currentTime = 0;
    if (v.paused) v.play?.().catch(() => {}); else v.pause?.();
  };

  const toggleMute = () => {
    const v = videoRef?.current;
    if (v) v.muted = !v.muted;
  };

  const goFullscreen = () => {
    const v = videoRef?.current;
    if (!v) return;
    (v.requestFullscreen || v.webkitRequestFullscreen ||
     v.webkitEnterFullscreen || v.mozRequestFullScreen)?.call(v);
  };

  const btn = {
    display: "flex", alignItems: "center", justifyContent: "center",
    background: "transparent", border: "none", cursor: "pointer",
    color: "#cbd5e1", padding: 0, lineHeight: 1, flex: "0 0 auto",
  };

  const pct = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: compact ? "5px 8px" : "7px 10px",
      background: "#0a0f1a", borderRadius: 6, marginTop: 6,
      border: "1px solid #17202f", userSelect: "none",
    }}>
      <button onClick={togglePlay} title={paused ? "Play" : "Pause"}
              style={{ ...btn, fontSize: compact ? 13 : 15, width: compact ? 18 : 22 }}>
        {paused ? "▶" : "❚❚"}
      </button>
      <div
        ref={barRef}
        onMouseDown={onDown}
        title="Click to seek"
        style={{
          position: "relative", flex: 1, height: compact ? 6 : 8,
          background: "#1e293b", borderRadius: 99, cursor: dur ? "pointer" : "default",
        }}
      >
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0, width: `${pct}%`,
          background: "#3b82f6", borderRadius: 99,
        }} />
        <div style={{
          position: "absolute", left: `${pct}%`, top: "50%",
          width: compact ? 10 : 12, height: compact ? 10 : 12,
          marginLeft: compact ? -5 : -6, marginTop: compact ? -5 : -6,
          background: "#fff", borderRadius: "50%",
          boxShadow: "0 0 0 2px #3b82f6",
        }} />
      </div>
      <span style={{
        fontSize: compact ? 10 : 11, fontWeight: 800, color: "#cbd5e1",
        fontFamily: "monospace", whiteSpace: "nowrap", letterSpacing: ".02em",
      }}>
        {fmt(cur)} / {fmt(dur)}
      </span>
      <button onClick={toggleMute} title={muted ? "Unmute" : "Mute"}
              style={{ ...btn, fontSize: compact ? 12 : 14 }}>
        {muted ? "🔇" : "🔊"}
      </button>
      <button onClick={goFullscreen} title="Fullscreen"
              style={{ ...btn, fontSize: compact ? 12 : 14 }}>
        ⛶
      </button>
    </div>
  );
}
