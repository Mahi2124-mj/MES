// ───────────────────────────────────────────────────────────────────────
// BinFillingPage.jsx   (Bin Filling — BinVision integration)
// ───────────────────────────────────────────────────────────────────────
// The standalone BinVision bin-filling app runs on the server (:8090). The MES
// backend reverse-proxies it under /api/binfilling/* (routers/binfilling.py), so
// here we simply embed that proxied dashboard as a native MES page — it rides
// the same origin, tunnel and login as the rest of MES.
//
// Auth: the proxy requires a valid MES token. We drop the current MES token into
// a short-path cookie (mes_bv) BEFORE the iframe loads, so every same-origin
// request the embedded dashboard makes carries it. Nothing about BinVision is
// changed; this is the only glue.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState } from "react";
import PageTopbar from "../components/PageTopbar";

export default function BinFillingPage() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const t = sessionStorage.getItem("mes_token") || "";
      // Scoped to the proxy path only, so it is sent with the embedded
      // dashboard's requests and nothing else.
      if (t) document.cookie = `mes_bv=${t}; path=/api/binfilling; SameSite=Lax`;
    } catch (e) { /* ignore — proxy will 401 and show the login-needed state */ }
    setReady(true);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100dvh" }}>
      <PageTopbar leading="Bin" accent="Filling" />
      {ready && (
        <iframe
          title="Bin Filling"
          src="/api/binfilling/"
          style={{ flex: 1, width: "100%", border: "none", background: "#0f172a" }}
          allow="fullscreen"
        />
      )}
    </div>
  );
}
