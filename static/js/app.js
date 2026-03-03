/* speedRouter – frontend logic */
"use strict";

// ── Utilities ────────────────────────────────────────────────────────────────

function showAlert(containerId, message, type = "danger") {
  const el = document.getElementById(containerId);
  el.innerHTML = `<div class="alert alert-${type} py-2">${message}</div>`;
}

function clearEl(id) {
  document.getElementById(id).innerHTML = "";
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

// ── localStorage helpers (gateway + username only – never password) ───────────

const LS_GATEWAY  = "speedrouter_gateway";
const LS_USERNAME = "speedrouter_username";

function savePrefs(gateway, username) {
  try {
    localStorage.setItem(LS_GATEWAY,  gateway);
    localStorage.setItem(LS_USERNAME, username);
  } catch { /* ignore storage errors */ }
}

function loadPrefs() {
  try {
    return {
      gateway:  localStorage.getItem(LS_GATEWAY)  || "",
      username: localStorage.getItem(LS_USERNAME) || "",
    };
  } catch {
    return { gateway: "", username: "" };
  }
}

// ── Auto-fill on page load ───────────────────────────────────────────────────

(async function initForm() {
  const gwField       = document.getElementById("f-gateway");
  const userField     = document.getElementById("f-username");
  const gwHint        = document.getElementById("f-gateway-hint");
  const prefs         = loadPrefs();

  // Pre-fill saved values first
  if (prefs.gateway)  gwField.value   = prefs.gateway;
  if (prefs.username) userField.value = prefs.username;

  // Auto-detect gateway only if field is still empty
  if (!gwField.value) {
    try {
      const resp = await fetch("/api/network/gateway");
      const data = await resp.json();
      if (data.ok && data.gateway && !gwField.value) {
        gwField.value = data.gateway;
        if (gwHint) gwHint.classList.remove("d-none");
      }
    } catch { /* network unavailable – silently skip */ }
  }
})();

// ── Connection state ─────────────────────────────────────────────────────────

let connected = false;

function setConnected(gateway) {
  connected = true;
  document.getElementById("conn-badge").className = "badge bg-success";
  document.getElementById("conn-badge").textContent = `Connected: ${gateway}`;
  document.getElementById("btn-disconnect").classList.remove("d-none");
}

function setDisconnected() {
  connected = false;
  document.getElementById("conn-badge").className = "badge bg-secondary";
  document.getElementById("conn-badge").textContent = "Not connected";
  document.getElementById("btn-disconnect").classList.add("d-none");
}

// ── Connect form ─────────────────────────────────────────────────────────────

document.getElementById("connect-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearEl("connect-alert");

  const gateway  = document.getElementById("f-gateway").value.trim();
  const username = document.getElementById("f-username").value.trim();
  const password = document.getElementById("f-password").value;

  if (!gateway || !username || !password) {
    showAlert("connect-alert", "Please fill in all fields.");
    return;
  }

  const btn = e.submitter;
  btn.disabled = true;
  btn.textContent = "Connecting…";

  try {
    const data = await postJSON("/api/connect", { gateway, username, password });
    if (data.ok) {
      savePrefs(gateway, username);
      setConnected(data.gateway);
      showAlert("connect-alert", `✅ Connected to <strong>${data.gateway}</strong>`, "success");
      document.getElementById("f-password").value = "";
    } else {
      showAlert("connect-alert", `❌ ${data.error}`);
    }
  } catch {
    showAlert("connect-alert", "❌ Network error — could not reach the server.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect";
  }
});

// ── Disconnect ───────────────────────────────────────────────────────────────

document.getElementById("btn-disconnect").addEventListener("click", async () => {
  await postJSON("/api/disconnect", {});
  setDisconnected();
  showAlert("connect-alert", "Disconnected.", "secondary");
});

// ── Optimise ─────────────────────────────────────────────────────────────────

document.getElementById("btn-optimize").addEventListener("click", async () => {
  clearEl("optimize-results");

  if (!connected) {
    document.getElementById("optimize-results").innerHTML =
      `<div class="alert alert-warning">Connect to your modem first (use the 🔌 Connect tab).</div>`;
    return;
  }

  const btn = document.getElementById("btn-optimize");
  btn.disabled = true;
  btn.textContent = "Applying…";

  try {
    const data = await postJSON("/api/optimize", {});
    if (data.ok) {
      const rows = data.results.map((r) => {
        const icon = r.status.startsWith("applied") ? "✅" : "⚠️";
        return `<div class="result-row"><span>${icon}</span><span><strong>${r.setting}</strong> — ${r.status}</span></div>`;
      }).join("");
      document.getElementById("optimize-results").innerHTML =
        `<div class="card bg-dark border-secondary p-3 mt-2">${rows}</div>`;
    } else {
      document.getElementById("optimize-results").innerHTML =
        `<div class="alert alert-danger">❌ ${data.error}</div>`;
    }
  } catch {
    document.getElementById("optimize-results").innerHTML =
      `<div class="alert alert-danger">❌ Network error.</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Apply Recommended Settings";
  }
});

// ── Speed test ───────────────────────────────────────────────────────────────

document.getElementById("btn-speedtest").addEventListener("click", async () => {
  clearEl("speedtest-results");
  const spinner = document.getElementById("speedtest-spinner");
  const btn = document.getElementById("btn-speedtest");

  spinner.classList.remove("d-none");
  btn.disabled = true;

  try {
    const data = await postJSON("/api/speedtest", {});
    spinner.classList.add("d-none");

    if (data.ok) {
      document.getElementById("speedtest-results").innerHTML = `
        <div class="d-flex gap-3 flex-wrap mt-3">
          <div class="speed-tile download">
            <div class="value">${data.download_mbps}</div>
            <span class="unit">Mbps</span>
            <div class="label">⬇ Download</div>
          </div>
          <div class="speed-tile upload">
            <div class="value">${data.upload_mbps}</div>
            <span class="unit">Mbps</span>
            <div class="label">⬆ Upload</div>
          </div>
          <div class="speed-tile ping">
            <div class="value">${data.ping_ms}</div>
            <span class="unit">ms</span>
            <div class="label">🏓 Ping</div>
          </div>
        </div>
        <p class="text-muted small mt-2">Test server: ${data.server}</p>`;
    } else {
      document.getElementById("speedtest-results").innerHTML =
        `<div class="alert alert-danger mt-2">❌ ${data.error}</div>`;
    }
  } catch {
    spinner.classList.add("d-none");
    document.getElementById("speedtest-results").innerHTML =
      `<div class="alert alert-danger mt-2">❌ Network error.</div>`;
  } finally {
    btn.disabled = false;
  }
});

// ── VPN form ─────────────────────────────────────────────────────────────────

document.getElementById("vpn-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearEl("vpn-alert");

  if (!connected) {
    showAlert("vpn-alert", "Connect to your modem first (use the 🔌 Connect tab).", "warning");
    return;
  }

  const payload = {
    endpoint:    document.getElementById("v-endpoint").value.trim(),
    public_key:  document.getElementById("v-pubkey").value.trim(),
    private_key: document.getElementById("v-privkey").value.trim(),
    allowed_ips: document.getElementById("v-allowed").value.trim(),
    dns:         document.getElementById("v-dns").value.trim(),
  };

  if (!payload.endpoint || !payload.public_key || !payload.private_key) {
    showAlert("vpn-alert", "Endpoint, public key and private key are required.");
    return;
  }

  const btn = e.submitter;
  btn.disabled = true;
  btn.textContent = "Pushing…";

  try {
    const data = await postJSON("/api/vpn/config", payload);
    if (data.ok) {
      showAlert("vpn-alert", `✅ ${data.message}`, "success");
      document.getElementById("v-privkey").value = "";
    } else {
      showAlert("vpn-alert", `❌ ${data.error}`);
    }
  } catch {
    showAlert("vpn-alert", "❌ Network error.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Push VPN Config to Modem";
  }
});
