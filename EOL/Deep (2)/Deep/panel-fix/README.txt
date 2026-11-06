TBDI Panel Fix  (v1.1, 02-Nov-2026)
===================================
Panel screen par blink hone wali chhoti white lines band karta hai
(bytello 4K panels, Mali GPU). Har panel par sirf EK baar karna hai.

Pendrive se (ya browser se: http://192.168.30.15:5656/TBDI-Panel-Fix.apk)
--------------------------------------------------------------------------
1. Pendrive panel me lagao. File Manager me TBDI-Panel-Fix.apk kholo -> Install.
   (Pucche "unknown apps" ki permission -> Allow.)  Install ke baad "Open" dabao.
2. App me "Fix status" dikhega.  OFF ho to "Open WebView flags" dabao.
   Pehli baar dialog aaye to: OK -> Allow.
3. Search box me  gpu  likho.
4. "disable-gpu-rasterization" ke neeche "Default" ko  Enabled  karo.
5. Back dabao -> app me "Fix status: ON" aana chahiye.
6. Panel restart karo (power off, phir on).  Bas.

Wapas normal karna ho: app -> "Open WebView flags" -> "Reset flags" -> panel restart.

Note
----
- Ye setting panel ke WebView me save hoti hai; restart / WebView update ke baad bhi rehti hai.
- Panel par "WebView developer mode" ka notification dikhega - normal hai.
- Android kisi app ko ye flag khud badalne nahi deta, isliye step 3-4 haath se karna padta hai.
