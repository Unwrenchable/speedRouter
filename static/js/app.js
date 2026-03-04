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

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

  // Restore connected state from server session (survives page reload)
  try {
    const statusResp = await fetch("/api/status");
    const statusData = await statusResp.json();
    if (statusData.connected) {
      setConnected(statusData.gateway);
    }
  } catch { /* offline or server unavailable – silently skip */ }

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

function setConnected(gateway, verified = true) {
  connected = true;
  const badge = document.getElementById("conn-badge");
  if (verified) {
    badge.className = "badge bg-success";
    badge.textContent = `Connected: ${gateway}`;
  } else {
    badge.className = "badge bg-warning text-dark";
    badge.textContent = `⚠️ Saved: ${gateway}`;
  }
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
      setConnected(data.gateway, data.verified === true);
      document.getElementById("f-password").value = "";
      if (data.verified === true) {
        showAlert("connect-alert", `✅ Connected to <strong>${data.gateway}</strong>`, "success");
      } else {
        showAlert("connect-alert",
          `⚠️ Credentials saved for <strong>${data.gateway}</strong>, but the modem was not reachable from this server. ` +
          `For full modem management, run speedRouter locally on your home network.`,
          "warning");
      }
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

// ── Robocall Shield ───────────────────────────────────────────────────────────

async function renderRobocallList() {
  const container = document.getElementById("robocall-list");
  try {
    const data = await (await fetch("/api/robocall/list")).json();
    if (!data.ok || data.entries.length === 0) {
      container.innerHTML = `<p class="text-muted small">No blocked IPs yet. Add entries above.</p>`;
      return;
    }
    const rows = data.entries.map((e) => `
      <tr>
        <td class="small">${escapeHtml(e.label)}</td>
        <td class="small font-monospace">${escapeHtml(e.cidr)}</td>
        <td class="small text-muted">${escapeHtml(e.added)}</td>
        <td><button type="button" class="btn btn-sm btn-outline-danger py-0 rb-remove"
            data-cidr="${escapeHtml(e.cidr)}">Remove</button></td>
      </tr>`).join("");
    container.innerHTML = `
      <table class="table table-dark table-sm table-bordered mb-0">
        <thead><tr><th>Label</th><th>IP / CIDR</th><th>Added</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch {
    container.innerHTML = `<p class="text-muted small">Could not load blocklist.</p>`;
  }
}

// Event delegation for dynamically rendered Remove buttons
document.getElementById("robocall-list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".rb-remove");
  if (!btn) return;
  const cidr = btn.dataset.cidr;
  try {
    const data = await postJSON("/api/robocall/unblock", { cidr });
    if (data.ok) await renderRobocallList();
  } catch { /* ignore */ }
});

document.getElementById("robocall-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearEl("robocall-alert");

  const cidr  = document.getElementById("rb-cidr").value.trim();
  const label = document.getElementById("rb-label").value.trim();

  if (!cidr) {
    showAlert("robocall-alert", "Please enter an IP address or CIDR.");
    return;
  }

  try {
    const data = await postJSON("/api/robocall/block", { cidr, label });
    if (data.ok) {
      document.getElementById("rb-cidr").value  = "";
      document.getElementById("rb-label").value = "";
      await renderRobocallList();
    } else {
      showAlert("robocall-alert", `❌ ${data.error}`);
    }
  } catch {
    showAlert("robocall-alert", "❌ Network error.");
  }
});

document.getElementById("btn-robocall-push").addEventListener("click", async () => {
  clearEl("robocall-alert");

  if (!connected) {
    showAlert("robocall-alert", "Connect to your modem first (use the 🔌 Connect tab).", "warning");
    return;
  }

  const btn = document.getElementById("btn-robocall-push");
  btn.disabled = true;
  btn.textContent = "Pushing…";

  try {
    const data = await postJSON("/api/robocall/push", {});
    if (data.ok) {
      if (data.message) {
        showAlert("robocall-alert", `ℹ️ ${data.message}`, "info");
      } else {
        const rows = data.results.map((r) => {
          const icon = r.status.startsWith("pushed") ? "✅" : "⚠️";
          return `<div class="result-row"><span>${icon}</span><span><strong>${escapeHtml(r.entry)}</strong> (${escapeHtml(r.cidr)}) — ${escapeHtml(r.status)}</span></div>`;
        }).join("");
        document.getElementById("robocall-alert").innerHTML =
          `<div class="card bg-dark border-secondary p-3 mt-2">${rows}</div>`;
      }
    } else {
      showAlert("robocall-alert", `❌ ${data.error}`);
    }
  } catch {
    showAlert("robocall-alert", "❌ Network error.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Push Rules to Modem";
  }
});

// Load the robocall list whenever the tab is first shown, and on initial page load
document.getElementById("tab-robocall-btn").addEventListener("shown.bs.tab", renderRobocallList);
renderRobocallList();

// ── DSL Diagnostics ───────────────────────────────────────────────────────────

function renderDslData(data) {
  const el = document.getElementById("dsl-results");
  if (!data.ok) {
    el.innerHTML = `<div class="alert alert-danger">❌ ${escapeHtml(data.error)}</div>`;
    return;
  }
  // Pretty-print the raw JSON from the modem so users can see every field.
  const json = JSON.stringify(data.data, null, 2);
  el.innerHTML = `
    <div class="card bg-black border-secondary mt-2">
      <div class="card-header text-muted small py-1">Raw DSL stats from modem</div>
      <pre class="p-3 mb-0 text-success small" style="white-space:pre-wrap;word-break:break-all;">${escapeHtml(json)}</pre>
    </div>`;
}

document.getElementById("btn-dsl-status").addEventListener("click", async () => {
  const spinner = document.getElementById("dsl-spinner");
  const btn = document.getElementById("btn-dsl-status");

  if (!connected) {
    document.getElementById("dsl-results").innerHTML =
      `<div class="alert alert-warning">Connect to your modem first (use the 🔌 Connect tab).</div>`;
    return;
  }

  spinner.classList.remove("d-none");
  btn.disabled = true;

  try {
    const data = await (await fetch("/api/dsl/status")).json();
    renderDslData(data);
  } catch {
    document.getElementById("dsl-results").innerHTML =
      `<div class="alert alert-danger">❌ Network error.</div>`;
  } finally {
    spinner.classList.add("d-none");
    btn.disabled = false;
  }
});

document.getElementById("btn-dsl-retrain").addEventListener("click", async () => {
  const spinner = document.getElementById("dsl-spinner");
  const btn = document.getElementById("btn-dsl-retrain");
  const resultsEl = document.getElementById("dsl-results");

  if (!connected) {
    resultsEl.innerHTML =
      `<div class="alert alert-warning">Connect to your modem first (use the 🔌 Connect tab).</div>`;
    return;
  }

  spinner.classList.remove("d-none");
  btn.disabled = true;
  btn.textContent = "Retraining…";

  try {
    const data = await postJSON("/api/dsl/retrain", {});
    if (data.ok) {
      resultsEl.innerHTML = `<div class="alert alert-success">✅ ${escapeHtml(data.message)}</div>`;
    } else {
      resultsEl.innerHTML = `<div class="alert alert-danger">❌ ${escapeHtml(data.error)}</div>`;
    }
  } catch {
    resultsEl.innerHTML = `<div class="alert alert-danger">❌ Network error.</div>`;
  } finally {
    spinner.classList.add("d-none");
    btn.disabled = false;
    btn.textContent = "Retrain Lines";
  }
});

// ── VPN Server ────────────────────────────────────────────────────────────────

async function loadVpnServerStatus() {
  const statusEl = document.getElementById("vpnsvr-status");
  const configSection = document.getElementById("vpnsvr-config-section");
  const peersSection  = document.getElementById("vpnsvr-peers-section");
  try {
    const data = await (await fetch("/api/vpn/server/status")).json();
    if (!data.ok) { statusEl.innerHTML = `<div class="alert alert-danger">❌ ${escapeHtml(data.error)}</div>`; return; }
    if (!data.initialized) {
      statusEl.innerHTML = `<div class="alert alert-info">ℹ️ VPN server not yet initialised. Fill in the form below and click <strong>(Re-)Generate Server Keys</strong>.</div>`;
      configSection.classList.add("d-none");
      peersSection.classList.add("d-none");
    } else {
      const runBadge = data.running
        ? `<span class="badge bg-success ms-2">▶ Running</span>`
        : `<span class="badge bg-secondary ms-2">⏹ Stopped</span>`;
      statusEl.innerHTML = `
        <div class="alert alert-success py-2">
          ✅ VPN server initialised${runBadge}<br>
          <span class="font-monospace small">${escapeHtml(data.public_key)}</span><br>
          <span class="text-muted small">Subnet: ${escapeHtml(data.subnet)} · Port: ${escapeHtml(String(data.port))} · Peers: ${data.peer_count}</span>
        </div>`;
      configSection.classList.remove("d-none");
      peersSection.classList.remove("d-none");
      await loadVpnServerConfig();
      await renderVpnPeerList();
    }
  } catch {
    statusEl.innerHTML = `<div class="alert alert-danger">❌ Network error.</div>`;
  }
}

async function loadVpnServerConfig() {
  try {
    const data = await (await fetch("/api/vpn/server/config")).json();
    if (data.ok) document.getElementById("vpnsvr-config-text").textContent = data.config;
  } catch { /* ignore */ }
}

async function renderVpnPeerList() {
  const container = document.getElementById("vpnsvr-peer-list");
  try {
    const data = await (await fetch("/api/vpn/peers")).json();
    if (!data.ok || data.peers.length === 0) {
      container.innerHTML = `<p class="text-muted small">No peers yet. Add one above.</p>`;
      return;
    }
    const rows = data.peers.map((p) => `
      <tr>
        <td class="small">${escapeHtml(p.name)}</td>
        <td class="small font-monospace">${escapeHtml(p.address)}</td>
        <td class="small text-muted">${escapeHtml(p.added)}</td>
        <td class="small"><button type="button" class="btn btn-sm btn-outline-info py-0 vp-dl"
            data-id="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">⬇ Config</button></td>
        <td><button type="button" class="btn btn-sm btn-outline-danger py-0 vp-remove"
            data-id="${escapeHtml(p.id)}">Remove</button></td>
      </tr>`).join("");
    container.innerHTML = `
      <table class="table table-dark table-sm table-bordered mb-0">
        <thead><tr><th>Name</th><th>Tunnel IP</th><th>Added</th><th></th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch {
    container.innerHTML = `<p class="text-muted small">Could not load peers.</p>`;
  }
}

// Init form
document.getElementById("vpnsvr-init-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearEl("vpnsvr-alert");
  const btn = e.submitter;
  btn.disabled = true;
  btn.textContent = "Generating…";
  try {
    const data = await postJSON("/api/vpn/server/init", {
      endpoint: document.getElementById("vs-endpoint").value.trim(),
      port: parseInt(document.getElementById("vs-port").value, 10),
      subnet: document.getElementById("vs-subnet").value.trim(),
      dns: document.getElementById("vs-dns").value.trim(),
    });
    if (data.ok) {
      showAlert("vpnsvr-alert", `✅ Server keys generated. Public key: <code>${escapeHtml(data.public_key)}</code>`, "success");
      await loadVpnServerStatus();
    } else {
      showAlert("vpnsvr-alert", `❌ ${escapeHtml(data.error)}`);
    }
  } catch {
    showAlert("vpnsvr-alert", "❌ Network error.");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔑 (Re-)Generate Server Keys";
  }
});

// Apply on host button
document.getElementById("btn-vpnsvr-apply").addEventListener("click", async () => {
  clearEl("vpnsvr-alert");
  const btn = document.getElementById("btn-vpnsvr-apply");
  btn.disabled = true;
  btn.textContent = "Applying…";
  try {
    const data = await postJSON("/api/vpn/server/apply", {});
    if (data.ok) {
      showAlert("vpnsvr-alert", `✅ ${escapeHtml(data.message)}`, "success");
      await loadVpnServerStatus();
    } else {
      showAlert("vpnsvr-alert", `❌ ${escapeHtml(data.error)}`);
    }
  } catch {
    showAlert("vpnsvr-alert", "❌ Network error.");
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Apply on this Host";
  }
});

// Copy server config button
document.getElementById("btn-vpnsvr-copy-config").addEventListener("click", () => {
  const text = document.getElementById("vpnsvr-config-text").textContent;
  navigator.clipboard.writeText(text).then(
    () => showAlert("vpnsvr-alert", "✅ Server config copied to clipboard.", "success"),
    () => showAlert("vpnsvr-alert", "❌ Could not access clipboard.", "warning"),
  );
});

// Add peer form
document.getElementById("vpnsvr-peer-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearEl("vpnsvr-alert");
  const name = document.getElementById("vp-name").value.trim();
  if (!name) { showAlert("vpnsvr-alert", "Please enter a peer name."); return; }
  const btn = e.submitter;
  btn.disabled = true;
  btn.textContent = "Adding…";
  try {
    const data = await postJSON("/api/vpn/peers/add", { name });
    if (data.ok) {
      document.getElementById("vp-name").value = "";
      showAlert("vpnsvr-alert", `✅ Peer <strong>${escapeHtml(data.peer.name)}</strong> added — tunnel IP: <code>${escapeHtml(data.peer.address)}</code>`, "success");
      await renderVpnPeerList();
      await loadVpnServerConfig();
    } else {
      showAlert("vpnsvr-alert", `❌ ${escapeHtml(data.error)}`);
    }
  } catch {
    showAlert("vpnsvr-alert", "❌ Network error.");
  } finally {
    btn.disabled = false;
    btn.textContent = "➕ Add Peer";
  }
});

// Event delegation: download config & remove buttons in peer list
document.getElementById("vpnsvr-peer-list").addEventListener("click", async (e) => {
  const dlBtn = e.target.closest(".vp-dl");
  if (dlBtn) {
    const id   = dlBtn.dataset.id;
    const name = dlBtn.dataset.name;
    try {
      const data = await (await fetch(`/api/vpn/peers/${encodeURIComponent(id)}/config`)).json();
      if (data.ok) {
        const blob = new Blob([data.config], { type: "text/plain" });
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href     = url;
        a.download = `${name.replace(/\s+/g, "_")}-wg0.conf`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        showAlert("vpnsvr-alert", `❌ ${escapeHtml(data.error)}`);
      }
    } catch {
      showAlert("vpnsvr-alert", "❌ Network error.");
    }
    return;
  }

  const rmBtn = e.target.closest(".vp-remove");
  if (rmBtn) {
    const id = rmBtn.dataset.id;
    try {
      const data = await postJSON("/api/vpn/peers/remove", { id });
      if (data.ok) {
        await renderVpnPeerList();
        await loadVpnServerConfig();
      } else {
        showAlert("vpnsvr-alert", `❌ ${escapeHtml(data.error)}`);
      }
    } catch {
      showAlert("vpnsvr-alert", "❌ Network error.");
    }
  }
});

// Load VPN server status when the tab is shown
document.getElementById("tab-vpnsvr-btn").addEventListener("shown.bs.tab", loadVpnServerStatus);
