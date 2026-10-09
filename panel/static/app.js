(function () {
  const codeEl    = document.getElementById("code");
  const expiryEl  = document.getElementById("expiry");
  const waitingEl = document.getElementById("waiting");
  let active = "";

  async function start() {
    let r;
    try {
      const k = new URLSearchParams(location.search).get("k") || "";
      r = await fetch("/api/new?k=" + encodeURIComponent(k), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: TPL, c: CAMP })
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
    } catch (e) {
      codeEl.textContent    = "ERROR";
      waitingEl.textContent = "Could not reach the server. Reload the page.";
      return;
    }

    const d = await r.json();
    codeEl.textContent = d.user_code;
    active = d.user_code;

    let secs = d.expires_in;
    setInterval(() => {
      if (secs <= 0) { expiryEl.textContent = "Code expired"; return; }
      secs--;
      const m = String(Math.floor(secs / 60)).padStart(2, "0");
      const s = String(secs % 60).padStart(2, "0");
      expiryEl.textContent = `Code expires in ${m}:${s}`;
    }, 1000);

    pollStatus();
  }

  async function pollStatus() {
    if (!active) return;
    try {
      const r = await fetch("/api/status/" + active);
      const d = await r.json();
      if (d.status === "success") {
        waitingEl.textContent = "✅ Verified. You may close this window.";
        return;
      }
      if (d.status && d.status !== "pending") {
        waitingEl.textContent = "⚠️ Code expired.";
        return;
      }
    } catch (_) {}
    setTimeout(pollStatus, 4000);
  }

  window.copyCode = function () {
    navigator.clipboard.writeText(active || "");
  };

  document.addEventListener("DOMContentLoaded", start);
})();