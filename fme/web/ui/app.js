
// When the gallery was last redrawn during a run, and whether what it
// is showing is a window onto a larger set.
let lastLiveDraw = 0;
// The job whose files the page has been building during a run, so a new
// run starts its own list rather than adding to the last one's.
let liveFor = null;
let liveTotal = 0;
const $ = id => document.getElementById(id);
// The nonce the page's one script tag was given. The case map loads
// MapLibre on demand, and a script added later runs under the page's
// policy only if it carries the same nonce.
const PAGE_NONCE = (document.currentScript && document.currentScript.nonce) || "";
let jobId = null, files = [], filter = "all", view = "gallery", timer = null;
// Which case the page is becoming. Every change of jobId takes a new one,
// and an answer for an older one is dropped: opening case A and then B
// before A's file list arrived put A's files under B's job, and a grading
// of one of them was written into B's case.db (v2026.11.319).
let pageTicket = 0;
function claimPage() { return ++pageTicket; }

// Every request that changes something carries this header. A browser
// will not add it to a request from another website without asking this
// server first, and the server never agrees, so the header is how the
// server knows the request came from this page. See request_guard.py.
function guarded(opts) {
  opts = Object.assign({}, opts || {});
  opts.headers = Object.assign({}, opts.headers || {}, {"X-FME-Request": "1"});
  return opts;
}

async function api(path, opts) {
  const res = await fetch(path, guarded(opts));
  const body = await res.text();
  let data;
  try {
    data = JSON.parse(body);
  } catch (e) {
    // Something answered with a page rather than data. Say what and from
    // where, because "Unexpected token '<'" tells an examiner nothing.
    const first = body.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    throw new Error(
      `${path} answered with ${res.status} ${res.statusText} and a page, not ` +
      `data${first ? `: ${first.slice(0, 180)}` : ""}. The console window this ` +
      `program was started from has the details.`);
  }
  if (!data.ok) throw new Error(data.error || `Unknown error from ${path}`);
  return data;
}
// A count that may not be in the response. Formatting one that is absent
// throws inside the handler, and what reaches the examiner is a message
// about this program's internals instead of the answer they asked for.
function count(n) {
  return typeof n === "number" ? n.toLocaleString() : "—";
}

// Escapes for both text and quoted attribute values. The old version
// (textContent -> innerHTML) left " and ' alone, and esc() is used inside
// attributes such as data-value="..." on EXIF Make/Model/Artist facets: a
// camera that wrote a quote and an event handler into its Make tag ran script in
// the examiner's session.
const ESC_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
// The quotes are written \x22 and \x27 in the pattern so that nothing reading
// this file as text (the markup test strips strings) takes them for strings.
function esc(s) { return String(s == null ? "" : s).replace(/[&<>\x22\x27]/g, c => ESC_MAP[c]); }

let caps = null;

(async () => {
  try {
    caps = await api("/api/capabilities");
    const caps_el = $("caps");
    if (caps_el) {
      let text = caps.filesystems.join(" · ");
      const extra = caps.vendor_fs || {};
      if (extra.available && (extra.kinds || []).length) {
        text += " · " + extra.kinds.join(" · ") +
                " (read by a vendored MIT reader, see the manual's credits)";
      }
      caps_el.textContent = text;
    }
    const missing = [];
    if (!caps.pytsk3) missing.push("pytsk3 for exFAT/NTFS/HFS+/APFS");
    if (!caps.pyewf) missing.push("libewf-python for .E01");
    $("containers").innerHTML = "Containers: " + esc(caps.containers.join(", ")) +
      (missing.length ? "<br>Install " + esc(missing.join(" and ")) + " to add the rest." : "");
    if (caps.default_path) $("path").value = caps.default_path;
    // What this machine remembered last time, so nobody retypes an address
    // and a certificate path every morning.
    const kept = caps.remembered || {};
    if (kept.store_path && !$("store-path").value) {
      $("store-path").value = kept.store_path;
    }
    if (kept.store_cert && !$("store-cert").value) {
      $("store-cert").value = kept.store_cert;
    }
    $("store-remember").checked = kept.token_saved || !kept.store_path;
    if (kept.token_saved) {
      $("store-token").placeholder =
        "remembered on this machine — leave blank to keep it";
    }
    // Connect to the store this machine used last time, without being
    // asked. An examiner starting a case should find the store already
    // there; making them press a button first is a step that exists only
    // because the program forgot.
    if (kept.store_path) connectRememberedStore(kept);
    // The release lived in the title that used to sit above this bar, so
    // it moves here rather than disappearing: somebody reporting a problem
    // has to be able to say which version they are on.
    const release = $("app-release");
    if (release) release.textContent = caps.release || "";
    $("ver-badge").textContent =
      `gui ${caps.gui_version} · core ${caps.core_version}`;
    $("ver-badge").title = `media_gui.py: ${caps.gui_path}\nmedia_core.py: ${caps.core_path}` +
      "\n\nMIT License, provided as is, without warranty. Includes The Sleuth Kit, " +
      "Copyright (c) 1997,1998,1999, International Business Machines Corporation " +
      "and others. Licenses: File tab → Licenses.";
    if (caps.core_warning) {
      $("core-warning").textContent = caps.core_warning;
      $("core-warning").style.display = "block";
    }
    if (!caps.supports.cancel) $("btn-cancel").title = "needs a newer media_core.py";
    if (caps.supports_workers === false) {
      $("workers").value = "1";
      $("workers").disabled = true;
      $("workers-hint").textContent = "This media_core.py carves single-process only.";
    } else {
      $("workers-hint").textContent =
        `${caps.cpu_count} core(s) detected — Auto uses ${caps.default_workers}. ` +
        `Carving is CPU-bound on the byte scan, so more workers finish a large ` +
        `image sooner, up to whatever the disk can feed.`;
    }
    if (!caps.supports.inspect) $("btn-load").disabled = true;
    if (!caps.supports.custom_signatures) {
      document.querySelector('.tab[data-tab="sigs"]').style.display = "none";
    }
  } catch (e) { $("caps").textContent = "capability check failed"; }
})();

// ---- load / inspect the image ------------------------------------------
let loadedPath = null;

$("btn-load").addEventListener("click", async () => {
  const path = $("path").value.trim();
  $("err").textContent = "";
  if (!path) { $("err").textContent = "Enter or browse to an image first."; return; }
  $("btn-load").disabled = true;
  $("load-state").textContent = "opening…";
  try {
    const d = await api("/api/inspect?path=" + encodeURIComponent(path));
    showLoad(d.info);
  } catch (e) {
    $("load-state").textContent = "failed";
    $("load-info").style.display = "none";
    $("err").textContent = e.message;
    loadedPath = null;
  } finally { $("btn-load").disabled = false; }
});

// ---- where results are written -----------------------------------------
async function refreshOutput(newPath) {
  try {
    const d = newPath
      ? await api("/api/output", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: newPath})})
      : await api("/api/output");
    $("out-root").value = d.path;
    const s = d.space || {};
    if (s.error) {
      $("out-space").textContent = s.error;
      return;
    }
    $("out-space").innerHTML =
      `${esc(s.free_human)} free of ${esc(s.total_human)}. Each run gets its ` +
      `own subfolder here.` +
      (s.tight
        ? `<br><span style="color:var(--warn)">That is less than a quarter of ` +
          `the image size — recovered files can approach the size of the ` +
          `source. Consider another drive.</span>`
        : "");
  } catch (e) {
    $("out-space").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`;
  }
}

$("btn-out-browse").addEventListener("click", () => {
  openBrowser(p => refreshOutput(p), $("out-root").value.trim(),
              "Choose a folder for results");
});
$("out-root").addEventListener("change", () => {
  const v = $("out-root").value.trim();
  if (v) refreshOutput(v);
});
refreshOutput();

// ---- attached drives and folders ---------------------------------------
$("btn-sources").addEventListener("click", async () => {
  const box = $("sources-box");
  if (box.style.display === "block") { box.style.display = "none"; return; }
  box.style.display = "block";
  box.innerHTML = '<p class="hint">Looking…</p>';
  try {
    const d = await api("/api/sources");
    if (!d.sources.length) {
      box.innerHTML = '<p class="hint">Nothing detected. Type a path or use Browse.</p>';
      return;
    }
    box.innerHTML =
      (d.elevated ? "" :
        '<p class="hint" style="color:var(--warn)">Not running elevated — raw ' +
        'devices will fail to open. Mount points still work.</p>') +
      d.sources.map((s, i) => `<div class="source-row" data-i="${i}">
        <span><span class="k">${s.kind === "device" ? "device" : "folder"}</span>
          ${s.removable ? '<span class="k">removable</span> ' : ""}
          <span class="p">${esc(s.path)}</span>
          ${s.fs_type ? " · " + esc(s.fs_type) : ""}${s.size_human ? " · " + esc(s.size_human) : ""}
          <br><span class="hint">${esc(s.note)}</span></span>
        <button class="secondary">Use</button></div>`).join("");
    box.querySelectorAll(".source-row").forEach(row =>
      row.addEventListener("click", () => {
        $("path").value = d.sources[parseInt(row.dataset.i, 10)].path;
        box.style.display = "none";
        loadedPath = null;
        $("btn-load").click();
      }));
  } catch (e) {
    box.innerHTML = `<p class="hint" style="color:var(--err)">${esc(e.message)}</p>`;
  }
});

// ---- accept the image --------------------------------------------------
let activePath = null;

$("btn-use").addEventListener("click", async () => {
  $("err").textContent = "";
  $("btn-use").disabled = true;
  try {
    const d = await api("/api/use", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: $("path").value.trim()})
    });
    setActive(d.path, d.info);
  } catch (e) {
    $("err").textContent = e.message;
  } finally { $("btn-use").disabled = false; }
});

$("btn-change").addEventListener("click", async () => {
  // A run still reading the image is refused by the server; the page must
  // not then say nothing is loaded while the run goes on holding the drive.
  try { await api("/api/release", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: "{}"}); }
  catch (e) { $("err").textContent = e.message; return; }
  setActive(null, null);
  $("load-info").style.display = "none";
  $("load-state").textContent = "No image loaded";
});

function setActive(path, info) {
  activePath = path;
  const on = Boolean(path);
  $("btn-use").style.display = "none";
  $("btn-change").style.display = on ? "inline-block" : "none";
  $("btn-load").style.display = on ? "none" : "inline-block";
  $("path").readOnly = on;
  $("btn-browse").disabled = on;
  $("btn-start").disabled = !on;
  $("accepted").style.display = on ? "block" : "none";
  if (on) {
    const name = path.split(/[\\/]/).pop();
    const extra = info
      ? `${esc(info.container)} · ${esc(info.size_human)}` +
        ((info.volumes || []).length
          ? ` · ${info.volumes.length} volume(s): ` +
            esc(info.volumes.map(v => v.fs || "unrecognized").join(", "))
          : " · no mountable file system (carving still works)")
      : "accepted";
    $("accepted").innerHTML =
      `✓ <strong>In use — ${esc(name)}</strong><br>${extra}<div class="p">${esc(path)}</div>`;
    $("load-state").innerHTML = '<span style="color:var(--ok)">accepted</span>';
    refreshOutput();          // re-check free space against this image
  }
}

// Warn before a reload or a close while work is in progress. The run itself
// survives -- it is a thread on the server, not in this page -- but people
// reasonably read a blank progress bar as a lost job, so it is better not to
// let the reload happen by accident.
let running = false;
window.addEventListener("beforeunload", (e) => {
  if (!running) return;
  e.preventDefault();
  e.returnValue = "An extraction is still running. It will carry on if you " +
                  "reload, and this page will rejoin it.";
  return e.returnValue;
});

// Restore state on load: the accepted image, and any job still in progress.
(async () => {
  try {
    const a = await api("/api/active");
    if (a.path) { $("path").value = a.path; setActive(a.path, a.info); }
    else $("btn-start").disabled = true;
  } catch (e) { $("btn-start").disabled = true; }

  const ticket = claimPage();
  try {
    const j = await api("/api/job/current");
    if (!j.job_id || ticket !== pageTicket) return;
    jobId = j.job_id;
    $("job-badge").style.display = "inline-block";
    $("job-badge").textContent = caseBadge();
    if (j.status === "running") {
      running = true;
      $("btn-start").disabled = true;
      $("btn-cancel").style.display = "inline-block";
      $("status").textContent = "rejoined a run already in progress…";
      poll();
    } else if (j.paged || (j.files && j.files.length)) {
      // A finished run from before the reload: put its results back.
      files = takeFiles(j, []);
      $("bar").style.width = "100%";
      $("status").textContent = `done — ${caseSize()} file(s) recovered ` +
                                `(from before this page was reloaded)`;
      showFinished(j.status, j.summary, caseSize());
      showSummary(j.summary, j.status);
      loadCase();
      $("export-box").style.display = "block";
      $("vic-box").style.display = "block";
      enterReview(j.summary && j.summary.image);
      render();
    }
  } catch (e) { /* nothing in progress */ }
})();

function showLoad(info) {
  loadedPath = info.ok ? info.path : null;
  $("load-state").innerHTML = info.ok
    ? `<span style="color:var(--accent)">readable</span> — ${esc(info.container)}, ${esc(info.size_human)} — press Use This Image`
    : `<span style="color:var(--err)">not readable</span>`;
  $("btn-use").style.display = info.ok ? "inline-block" : "none";

  const vols = (info.volumes || []).map(v => `<tr>
      <td class="k">${esc(v.label)}</td>
      <td>${esc(v.fs || "unrecognized")}</td>
      <td>${esc(v.size_human)}</td>
      <td>${esc(v.volume_label || v.description || "")}</td></tr>`).join("");

  $("load-info").innerHTML =
    `<table>
       <tr><td class="k">Container</td><td colspan="3">${esc(info.container)}${
         info.segments > 1 ? ` (${info.segments} segments)` : ""}</td></tr>
       <tr><td class="k">Size</td><td colspan="3">${esc(info.size_human)}</td></tr>
       <tr><td class="k">Partitioning</td><td colspan="3">${esc(info.partition_scheme || "none")}</td></tr>
       ${vols ? `<tr><td class="k" colspan="4" style="padding-top:6px;">Volumes</td></tr>${vols}` : ""}
     </table>` +
    (info.warnings || []).map(w => `<span class="warn">⚠ ${esc(w)}</span>`).join("");
  $("load-info").style.display = "block";
}

// re-check when the path changes so a stale panel never misleads
$("path").addEventListener("input", () => {
  if (loadedPath && $("path").value.trim() !== loadedPath) {
    loadedPath = null;
    $("btn-use").style.display = "none";
    $("load-state").textContent = "No image loaded";
    $("load-info").style.display = "none";
  }
});

// ---- tabs ---------------------------------------------------------------
document.querySelectorAll(".tab").forEach(t =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    $("tab-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "sigs") refreshActiveSignatures();
  }));

// ---- custom carve signatures --------------------------------------------
let activeSigs = [];

function addPathRow(value) {
  const row = document.createElement("div");
  row.className = "path-row";
  row.innerHTML = `<input type="text" placeholder="/path/to/sample_file">
                   <button class="secondary" data-act="browse">Browse…</button>
                   <button class="secondary" data-act="remove">✕</button>`;
  if (value) row.querySelector("input").value = value;
  row.querySelector('[data-act="remove"]').addEventListener("click", () => {
    row.remove();
    if (!$("sig-path-rows").children.length) addPathRow("");
  });
  row.querySelector('[data-act="browse"]').addEventListener("click", () => {
    openBrowser(picked => { row.querySelector("input").value = picked; });
  });
  $("sig-path-rows").appendChild(row);
}
addPathRow("");

$("btn-add-path-row").addEventListener("click", () => addPathRow(""));

$("btn-analyze-sigs").addEventListener("click", async () => {
  $("sig-analyze-error").textContent = "";
  const paths = [...$("sig-path-rows").querySelectorAll("input")]
    .map(i => i.value.trim()).filter(Boolean);
  if (!paths.length) {
    $("sig-analyze-error").textContent = "Add at least one sample file path.";
    return;
  }
  $("btn-analyze-sigs").disabled = true;
  $("btn-analyze-sigs").textContent = "Analyzing…";
  try {
    const d = await api("/api/signature/analyze", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths, scan_len: 32})
    });
    renderAnalysis(d, paths);
  } catch (e) {
    $("sig-analyze-error").textContent = e.message;
  } finally {
    $("btn-analyze-sigs").disabled = false;
    $("btn-analyze-sigs").textContent = "Analyze";
  }
});

function hexAscii(hex, ascii) {
  if (!hex) return '<span style="color:var(--muted);">(empty)</span>';
  return `${esc(hex)}<br><span style="color:var(--muted);">${esc(ascii || "")}</span>`;
}

function renderAnalysis(d, paths) {
  $("sig-analyze-results").style.display = "block";
  $("sig-sample-rows").innerHTML = (d.samples || []).map(s =>
    s.error
      ? `<tr><td>${esc(s.path)}</td><td colspan="3" style="color:var(--err);">${esc(s.error)}</td></tr>`
      : `<tr><td>${esc(s.path)}</td><td>${s.size}</td>
           <td>${hexAscii(s.header_hex, s.header_ascii)}</td>
           <td>${hexAscii(s.footer_hex, s.footer_ascii)}</td></tr>`).join("");

  const n = d.sample_count != null ? d.sample_count : (d.samples || []).length;
  $("sig-common-count").textContent =
    `Computed from ${n} readable sample(s). ` +
    (n > 1 ? "These are the bytes common to all of them — "
           : "Only one sample, so these are that file's own bytes — ") +
    "trim anything that varies between files before adding.";
  $("sig-header-hex").value = d.common_header_hex || "";
  $("sig-footer-hex").value = d.common_footer_hex || "";
  if (!$("sig-ext").value && paths.length) {
    const dot = paths[0].lastIndexOf(".");
    if (dot > -1) $("sig-ext").value = paths[0].slice(dot + 1);
  }
}

$("btn-save-sig").addEventListener("click", async () => {
  $("sig-save-error").textContent = "";
  $("btn-save-sig").disabled = true;
  try {
    const d = await api("/api/signature/save", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        ext: $("sig-ext").value.trim(),
        header_hex: $("sig-header-hex").value.trim(),
        footer_hex: $("sig-footer-hex").value.trim(),
        max_size: parseInt($("sig-max-size").value.trim() || "67108864", 10)
      })
    });
    activeSigs = d.signatures;
    renderActiveSigs();
    $("sig-ext").value = "";
  } catch (e) {
    $("sig-save-error").textContent = e.message;
  } finally { $("btn-save-sig").disabled = false; }
});

async function refreshActiveSignatures() {
  try {
    const d = await api("/api/signature/list");
    activeSigs = d.signatures;
    renderActiveSigs();
  } catch (e) { /* leave whatever is on screen */ }
}

function renderActiveSigs() {
  $("sig-active-rows").innerHTML = activeSigs.length ? activeSigs.map(s => `<tr>
      <td>.${esc(s.ext)}</td>
      <td>${esc(s.header_hex)}</td>
      <td>${s.footer_hex ? esc(s.footer_hex) : '<span style="color:var(--muted);">(none)</span>'}</td>
      <td>${esc(s.max_size_human || String(s.max_size))}</td>
      <td><button class="secondary" data-id="${s.id}">Remove</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="hint" style="padding:10px;">None yet — analyze a
        sample above, or type a header in by hand.</td></tr>`;

  $("sig-active-rows").querySelectorAll("button[data-id]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        const d = await api("/api/signature/delete", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({id: b.dataset.id})
        });
        activeSigs = d.signatures;
        renderActiveSigs();
      } catch (e) { $("sig-save-error").textContent = e.message; }
    }));

  const n = activeSigs.length;
  const line = n
    ? `Carving with the built-in signatures plus ${n} custom signature(s): `
      + activeSigs.map(s => "." + s.ext).join(", ") + "."
    : "Carving with the built-in image and video signatures only. "
      + "Add your own in the Custom Signatures tab.";
  $("sig-usage").textContent = n
    ? "These apply to every extraction started from this program until removed."
    : "";
  $("sig-inline-hint").textContent = line;
}

refreshActiveSignatures();

// ---- run ----------------------------------------------------------------
$("btn-start").addEventListener("click", async () => {
  const path = activePath || $("path").value.trim();
  $("err").textContent = "";
  if (!activePath) {
    $("err").textContent = "Load an image and press Use This Image first.";
    return;
  }
  $("btn-start").disabled = true;
  $("btn-cancel").style.display = "inline-block";
  $("summary").style.display = "none";
  $("finished").style.display = "none";
  $("manifest-link").style.display = "none";
  files = [];
  leavePaged();
  // A new run is a new case: nothing from the last one -- its grades, its
  // flags, its categories -- may show against this one's files. The grades
  // are keyed by hash, so a file this run shares with the last case would
  // have shown the last case's category until the run finished.
  decisions = {}; categories = []; caseFlags = []; caseCounts = {};
  lastCaseLoad = 0;
  caseGen++;          // any answer still in flight is for the last case
  caseFor = null;
  render();
  $("bar").style.width = "0%";
  $("status").textContent = "starting…";
  try {
    const d = await api("/api/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        path, mode: $("mode").value, align: parseInt($("align").value, 10),
        images_only: $("images-only").checked, videos_only: $("videos-only").checked,
        no_deleted: $("no-deleted").checked, include_system: $("include-system").checked,
        databases: $("databases").checked,
        hash_source: $("hash-source").checked,
        find_members: $("find-members").checked,
        carve_files: $("carve-files").checked,
        scan_inside: $("scan-inside").checked,
        artifacts: $("opt-artifacts").checked,
        workers: $("workers").value,
        output_root: $("out-root").value.trim(),
        case_number: $("case-number").value.trim(),
        examiner: $("case-examiner").value.trim(),
        evidence: $("case-evidence").value.trim(),
        auto_vic: $("auto-vic").checked,
        auto_lava: $("auto-lava").checked,
        // For this run only. Cleared below as soon as the run is accepted,
        // so it does not sit in the page for the next one.
        passwords: $("unlock-password").value ? [$("unlock-password").value] : [],
        key_files: $("unlock-keyfile").value.trim() ? [$("unlock-keyfile").value.trim()] : []
      })
    });
    $("unlock-password").value = "";
    claimPage();
    leaveReview();
    openedCase = null;
    jobId = d.job_id;
    running = true;
    $("job-badge").style.display = "inline-block";
    $("job-badge").textContent = caseBadge();
    poll();
  } catch (e) {
    $("err").textContent = e.message;
    $("btn-start").disabled = false;
    $("btn-cancel").style.display = "none";
  }
});

$("btn-cancel").addEventListener("click", async () => {
  $("btn-cancel").disabled = true;
  try { await api("/api/cancel", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({job_id: jobId}) }); } catch (e) {
    // The server says when a run cannot be stopped; show it rather than
    // leave the examiner believing it was.
    $("err").textContent = e.message;
  }
});

async function poll() {
  try {
    // Ask for the files after the ones already here, so the list grows to
    // the whole run rather than being a window of the newest 500.
    const from = (liveFor === jobId) ? files.length : 0;
    const d = await api(`/api/status?job_id=${jobId}&files_from=${from}`);
    pollFailures = 0;
    const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
    // What the run has done so far, beside what it is doing now. A long
    // run otherwise shows a phase and a percentage, which says nothing
    // about whether it is finding anything.
    const c = d.counts || {};
    const sofar = [
      c.walked ? `${count(c.walked)} walked` : "",
      c.carved ? `${count(c.carved)} carved` : "",
      c.from_databases ? `${count(c.from_databases)} from databases` : "",
      c.embedded ? `${count(c.embedded)} embedded` : "",
      c.deleted ? `${count(c.deleted)} deleted` : "",
      c.errors ? `${count(c.errors)} unreadable` : "",
    ].filter(Boolean).join(" · ");

    if (d.phase === "carve" && d.total) {
      $("bar").style.width = pct + "%";
      $("status").textContent =
        `carving — ${pct}% scanned, ${count(d.recovered)} recovered`;
    } else if (d.phase === "hash" && d.total) {
      $("bar").style.width = pct + "%";
      $("status").textContent = `hashing the source — ${pct}%`;
    } else if (d.phase === "filesystem") {
      $("status").textContent =
        `walking file systems — ${count(d.recovered)} recovered`;
    } else {
      $("status").textContent = `${d.status} — ${d.phase}`;
    }
    if ($("status-counts")) {
      $("status-counts").textContent = sofar;
      $("status-counts").style.display = sofar ? "block" : "none";
    }
    if (d.status === "running") {
      // Show what has been recovered so far.
      //
      // This branch used to update the status line and poll again,
      // and nothing else: `files` was only ever set when the run
      // finished or the examiner pressed Refresh. So a long run left
      // the gallery empty for hours while the counter climbed, which
      // reads as "the program is finding nothing".
      //
      // The server sends a window of the most recent files during a
      // run rather than the whole list, so this is a small response
      // however large the case. Drawn every few seconds, not on every
      // poll: redrawing a gallery twice a second while sixteen
      // workers carve helps nobody.
      //
      // The case's categories and tags, as soon as the case exists. They
      // were loaded only when the run finished, so for the whole of a run
      // -- hours, on a large image -- the category bar was "All" and
      // nothing else, and no file could be classified.
      if (caseFor !== jobId && Date.now() - lastCaseLoad > 4000) {
        lastCaseLoad = Date.now();
        loadCase();
      }
      //
      // Every batch is kept, whether or not this poll draws: the server
      // sends each file once, so a batch skipped here would be missing
      // from the gallery for the rest of the run.
      if (d.paged) {
        // Too many to send as they arrive: the gallery asks for its page,
        // every few seconds while the run adds to it.
        enterPaged(d.files_known);
        const now = Date.now();
        if (!lastLiveDraw || now - lastLiveDraw > 3000) {
          lastLiveDraw = now;
          render();
        }
      } else if (d.files_from !== undefined) {
        if (liveFor !== jobId || d.files_reset) { files = []; liveFor = jobId; }
        files = files.slice(0, d.files_from).concat(d.files || []);
        liveTotal = d.files_known || files.length;
        const now = Date.now();
        if (d.files && d.files.length && (!lastLiveDraw || now - lastLiveDraw > 3000)) {
          lastLiveDraw = now;
          render();
        }
        // Still catching up: ask again at once rather than in 600 ms.
        if (files.length < liveTotal) { timer = setTimeout(poll, 50); return; }
      }
      timer = setTimeout(poll, 600);
      return;
    }

    running = false;
    $("btn-start").disabled = false;
    $("btn-cancel").style.display = "none";
    $("btn-cancel").disabled = false;
    if (d.status === "error") { $("err").textContent = d.error; $("status").textContent = "failed"; return; }

    $("bar").style.width = "100%";
    // A finished run sends its whole list: that replaces what was built up.
    files = takeFiles(d, []);
    liveFor = null;
    $("status").textContent = (d.status === "canceled" ? "canceled — " : "done — ")
      + `${caseSize()} file(s) recovered`;
    showFinished(d.status, d.summary, caseSize());
    showSummary(d.summary, d.status);
    // Reveal the panels that only make sense once there are results. This
    // was missing, so the summary told people to use an Export panel that
    // stayed hidden.
    $("export-box").style.display = caseSize() ? "block" : "none";
    $("vic-box").style.display = caseSize() ? "block" : "none";
    if (caseSize()) enterReview(d.summary && d.summary.image);
    // Carry the case details entered before the run into the export panel,
    // so nothing has to be typed twice.
    [["case-number", "exp-case"], ["case-examiner", "exp-analyst"],
     ["case-evidence", "exp-evidence"]].forEach(([from, to]) => {
      if (!$(to).value.trim() && $(from).value.trim())
        $(to).value = $(from).value.trim();
    });
    render();
    await loadCase();
    // The hash store is checked as soon as a run finishes, not only when
    // somebody remembers to press "Check this case against the store": a
    // case run without that press had its known files -- earlier cases'
    // gradings, Project VIC, the NSRL -- shown as never seen (DC25-320).
    // A completed run only; a store that is absent or unreachable is passed
    // over, and the run stands either way.
    if (d.status === "done") autoStoreCheck();
  } catch (e) {
    // A failed poll is usually the page being reloaded or the server
    // restarting, not a failed job -- retry a few times before giving up.
    pollFailures += 1;
    if (pollFailures < 5) { setTimeout(() => poll(), 1500); return; }
    running = false;
    $("err").textContent = e.message;
    $("btn-start").disabled = false;
    $("btn-cancel").style.display = "none";
  }
}

let pollFailures = 0;

function takeFiles(d, fallback) {
  // The file list from a status answer -- unless it is a running job's
  // window of its newest files, which is not the list: taking it would
  // throw away everything the poll has built and leave 500.
  //
  // Or unless the case is too large to send, in which case there is no list
  // and the gallery asks for its pages (renderPaged).
  if (d.paged) { enterPaged(d.files_known); return []; }
  if (d.files_windowed) return files;
  if (paged) leavePaged();
  return d.files || fallback;
}

function showFinished(status, s, count) {
  const box = $("finished");
  const parts = [];
  if (s) {
    if (s.fs_files) parts.push(`${s.fs_files} from the file system`);
    if (s.carved_files) parts.push(`${s.carved_files} carved`);
    if (s.db_files) parts.push(`${s.db_files} from databases`);
    if (s.embedded_files) parts.push(`${s.embedded_files} embedded`);
    if (s.folder_files) parts.push(`${s.folder_files} from the folder`);
    if (s.package_files) parts.push(`${s.package_files} from the package`);
  }
  // Files the run found and could not keep. These were a console warning
  // each and nothing on this page, so a run onto a full drive read
  // "Finished" with evidence missing.
  const lost = s ? (s.write_failed || 0) + (s.read_failed || 0) : 0;
  const stopped = s && s.output_failure;
  box.innerHTML =
    (stopped
      ? `✗ <strong>Stopped — the output failed</strong> (${esc(s.output_failure)}). ` +
        `This case is INCOMPLETE: keeping ${count} file(s); free space or pick ` +
        `another output folder, then resume.`
      : status === "canceled"
      ? `■ <strong>Canceled</strong> — keeping ${count} file(s) already recovered`
      : `✓ <strong>Finished</strong> — ${count} file(s) recovered`) +
    (lost
      ? `<br><span style="color:var(--bad, #dc2626)"><strong>${lost} file(s) found ` +
        `but NOT recovered</strong> — ${s.write_failed || 0} could not be written, ` +
        `${s.read_failed || 0} could not be read from the evidence. Each is listed ` +
        `under "failures" in manifest.json.</span>` : "") +
    (parts.length ? `<br>${esc(parts.join(", "))}` : "") +
    (s && s.elapsed_seconds !== undefined ? ` · ${s.elapsed_seconds}s` : "") +
    `<br>Nothing is still running. Console lines after this are the browser ` +
    `loading previews.`;
  box.style.display = "block";
}

let lastSummary = null, openedCase = null;
// One word for what is open, "case", named by its case number or its folder:
// the badge used to show a job number that changed every time the same
// case was opened (v2026.11.320, U12).
function caseBadge() {
  const number = (openedCase && openedCase.case_number) || $("case-number").value.trim();
  const folder = (openedCase && openedCase.out_dir) ||
                 (lastSummary && lastSummary.output_dir) || "";
  const name = number || folder.split(/[\\/]/).filter(Boolean).pop() || "";
  return name ? "case " + name : "case open";
}
function showSummary(s, status) {
  if (!s) return;
  lastSummary = s;
  const vols = (s.volumes || []).map(v => v.fs + (v.label ? ` (${v.label})` : "")).join(", ");
  // The first line is what shows while reviewing, so it leads with what the
  // run kept, from whichever source it read -- a folder run read "0 from
  // the file system, 0 carved" over a gallery of its files.
  const kept = s.total_files !== undefined ? s.total_files : caseSize();
  const from = [[s.fs_files, "from the file system" + (s.fs_deleted ? ` (${s.fs_deleted} deleted)` : "")],
                [s.carved_files, "carved"], [s.folder_files, "from the folder"],
                [s.package_files, "from the package"], [s.db_files, "from databases"],
                [s.embedded_files, "embedded"]]
    .filter(([n]) => n).map(([n, what]) => `${Number(n).toLocaleString()} ${what}`);
  $("summary").innerHTML =
    `<span class="sumfirst"><strong>${Number(kept || 0).toLocaleString()}</strong> file(s)` +
    (from.length ? ` — ${esc(from.join(", "))}` : "") + `. </span>` +
    `<span class="sumtoggle-wrap"><button class="secondary sumtoggle" type="button" ` +
    `aria-expanded="false">Run summary ▾</button></span>` +
    `<span class="sumrest">` +
    (s.duplicates_suppressed
      ? `${s.duplicates_suppressed} further copy/copies of those same files were ` +
        `found — the bytes are stored once and every location is in ` +
        `duplicates.csv, so total findings are ` +
        `${(s.total_files || 0) + s.duplicates_suppressed}. ` : "") +
    `Took ` +
    `${s.elapsed_seconds}s over ${esc(s.image_size_human)}` +
    (status === "canceled" ? " (canceled early)" : "") +
    (vols ? `<br>Volumes: ${esc(vols)}` : "") +
    // The commonest reason a run finds nothing, said where the nothing is
    // reported rather than left for the examiner to work out.
    (s.advice
      ? `<br><span style="color:var(--warn)">${esc(s.advice)}</span>` : "") +
    `<br>Written to <code>${esc(s.output_dir)}</code>` +
    (s.project_vic
      ? `<br><span style="color:var(--ok)">ProjectVIC.json written — load that ` +
        `in Griffeye, not manifest.json.</span>`
      : s.reopened
      ? `<br><span class="hint">No ProjectVIC.json in this case's folder. ` +
        `Report / Export → Project VIC writes one.</span>`
      : (caseSize() === 0
          // Blaming the case number when the run found nothing sends an
          // examiner to fix the one thing that was not wrong.
          ? `<br><span style="color:var(--warn)">No ProjectVIC.json: the run ` +
            `recovered nothing, so there was nothing to write. A case number ` +
            `alone does not make a file.</span>`
          : `<br><span style="color:var(--warn)">No ProjectVIC.json: enter a ` +
            `case number before starting, or use Report / Export → Project VIC. ` +
            `Griffeye rejects manifest.json — it is this tool's own ` +
            `record.</span>`)) +
    (s.lava
      ? `<br><span style="color:var(--ok)">LAVA set written — open ` +
        `lava/_lava_data.lava.</span>` : "") +
    `</span>`;
  $("summary").classList.remove("open");
  $("summary").style.display = "block";
  $("manifest-link").href = `/api/manifest?job=${jobId}`;
  $("manifest-link").style.display = "inline";
}


// ---- reviewing a finished case ---------------------------------------------
// With a finished case open, the gallery is the work: the extraction form and
// the export panels step aside behind one line (#review-strip), the run
// summary shrinks to a line, and the details pane is kept on the right from
// the start, so picking the first file does not reflow every tile.
function enterReview(source) {
  document.body.classList.add("reviewing");
  document.body.classList.remove("show-settings", "show-panels");
  $("review-settings").setAttribute("aria-expanded", "false");
  $("review-panels").setAttribute("aria-expanded", "false");
  $("summary").classList.remove("open");
  $("review-source").textContent = source ? "Case from " + source : "";
  askWellbeingOnce();
  if ($("details-col").style.display === "none") {
    setDetailsVisible(true);
    if (pane === "details" && !selectedPath) $("details").innerHTML =
      '<p class="hint" style="margin:0;">Click a file to see its details. ' +
      'Double-click or press Enter to open it full size.</p>';
  }
}

function leaveReview() {
  document.body.classList.remove("reviewing", "show-settings", "show-panels");
}

function toggleReviewPart(cls, button) {
  const on = document.body.classList.toggle(cls);
  button.setAttribute("aria-expanded", on ? "true" : "false");
  return on;
}

$("review-settings").addEventListener("click", e => {
  const on = toggleReviewPart("show-settings", e.currentTarget);
  e.currentTarget.textContent = on ? "Hide extraction settings" : "Show extraction settings";
});
$("review-panels").addEventListener("click", e => {
  if (toggleReviewPart("show-panels", e.currentTarget))
    $("vic-box").scrollIntoView({block: "nearest"});
});
$("review-new").addEventListener("click", () => newExtraction());
function toggleSummary() {
  const open = $("summary").classList.toggle("open");
  const button = $("summary").querySelector(".sumtoggle");
  if (button) {
    button.textContent = open ? "Hide run summary ▴" : "Run summary ▾";
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
}
$("summary").addEventListener("click", e => {
  // The button, or the line itself; a link or a path inside is its own.
  if (e.target.closest("a, code")) return;
  if (!document.body.classList.contains("reviewing")) return;
  toggleSummary();
});

// ---- results ------------------------------------------------------------
document.querySelectorAll(".chip[data-filter]").forEach(c =>
  c.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-filter]").forEach(x => x.classList.remove("active"));
    c.classList.add("active"); filter = c.dataset.filter; render();
  }));


let searchTimer = null;
$("search").addEventListener("input", () => {
  // Debounced: typing a hash would otherwise re-filter on every character.
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { page = 0; render(); }, 180);
});
$("search").addEventListener("keydown", e => {
  if (e.key === "Escape") { $("search").value = ""; page = 0; render(); }
});

document.querySelectorAll(".chip[data-view]").forEach(c =>
  c.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-view]").forEach(x => x.classList.remove("active"));
    c.classList.add("active"); view = c.dataset.view; render();
  }));

// A page of results stays as it was built until the examiner leaves it.
// Without this, grading a file while filtered to Uncategorized removes it
// from the list, every tile after it shifts up, and the next click lands on
// something else. Working a page of five hundred becomes five hundred small
// corrections.
// /api/files serves one page, filtered and counted on the server
// (routes_media.py). The gallery does not use it yet: its filters --
// hiding known software, collapsing containers, similar groups,
// flags -- are applied in the browser and interlock, and paging
// through the server without moving that model too would give a
// gallery that pages correctly and filters wrongly. Moving it is the
// remaining work, and is not something to half-do.

let pinned = null;         // rel_paths of the page being worked
let pinKey = "";           // what the page was built from

function pageSignature(total) {
  // Everything that should rebuild a page -- and nothing that should not.
  // Decisions are deliberately absent: grading is what the page is for.
  return JSON.stringify([filter, catFilter, [...flagFilter].sort(),
                         Object.entries(facetFilter).map(
                           ([k, v]) => [k, [...v].sort()]).sort(),
                         ($("search").value || "").trim(),
                         $("per-page").value, page,
                         similarOnly ? [...similarOnly].length : 0, total]);
}

function unpin() {
  pinned = null;
  pinKey = "";
}

function paintGraded(digests) {
  // Repaint just the tiles that changed, rather than rebuilding the page.
  //
  // Every grading used to call render(), which rewrites the innerHTML of up
  // to five thousand tiles -- each with an image or a video element the
  // browser then has to fetch and lay out again. On a large page that is
  // seconds of blocked script per keystroke, and Chrome eventually stops
  // the page altogether with RESULT_CODE_HUNG. Nothing about one file
  // changing requires the other four thousand nine hundred to be rebuilt.
  const wanted = new Set(digests);
  const byPath = new Map();
  for (const file of (paged ? pv.files : files)) {
    if (wanted.has(file.sha256)) byPath.set(file.rel_path, file);
  }
  byPath.forEach((file, path) => {
    const tile = document.querySelector(`[data-pick="${CSS.escape(path)}"]`);
    if (!tile) return;
    const decided = decisions[file.sha256];
    const code = decided ? decided.category : uncategorizedCode;
    const category = categories.find(c => c.code === code);
    const graded = category && category.code !== uncategorizedCode;
    if (graded) tile.style.setProperty("--cat", category.color);
    else tile.style.removeProperty("--cat");

    let strip = tile.querySelector(".catstrip");
    if (graded && !strip) {
      strip = document.createElement("div");
      strip.className = "catstrip";
      tile.insertBefore(strip, tile.firstChild);
    } else if (!graded && strip) {
      strip.remove();
    }
    if (strip) {
      strip.classList.toggle("auto", !!(decided && decided.auto));
      strip.title = category ? category.name : "";
    }
    const tags = tile.querySelector(".tags");
    if (tags) tags.innerHTML = flagTags(file);
  });
  renderCategoryFilter();
  if (pane === "classify") renderClassification();
  refreshCounts();
  paintViewerGrade();
}

function nextAfterSelection() {
  // Where to go after grading a selection: the first file past the last one
  // selected that was not itself part of it. Grading fifteen files and
  // grading one are the same act at different sizes, so the cursor should
  // land in the same place either way -- ready for the next decision.
  const list = visible();
  let last = -1;
  list.forEach((file, index) => {
    if (selection.has(file.rel_path)) last = index;
  });
  if (last < 0) return null;
  for (let index = last + 1; index < list.length; index++) {
    if (!selection.has(list[index].rel_path)) return list[index];
  }
  // Past the end of a paged case's page: the files after it are the
  // server's to find.
  if (paged && list.length) return {after: list[list.length - 1].rel_path};
  return null;
}

function nextAfter(sha256) {
  // The file after the one being graded, as the list stands *now*. Worked
  // out before the grading changes what is visible: afterwards, the current
  // file has left the filtered list and looking for it lands one too far.
  const list = visible();
  let index = list.findIndex(f => f.rel_path === selectedPath);
  if (index < 0) index = list.findIndex(f => f.sha256 === sha256);
  if (index >= 0 && index + 1 < list.length) return list[index + 1];
  if (paged && index >= 0) return {after: list[index].rel_path};
  return null;
}

function moveTo(file) {
  if (!file) {
    if (selectedPath && pane === "details") showDetails(selectedPath);
    return;
  }
  if (file.after) {
    // A paged case, at the end of its page: ask for the page that starts
    // after this file, as the filter now stands, and go to its first file.
    pendingAfter = [file.after];
    unpin();
    renderPaged().then(() => {
      const area = $("results-scroll");
      if (area) area.scrollTo({top: 0});
      if (pv.files.length) moveTo(pv.files[0]);
    });
    return;
  }
  const onThisPage = document.querySelector(
    `[data-pick="${CSS.escape(file.rel_path)}"]`);
  if (!onThisPage) {
    // The next file is on another page, so the page genuinely has to be
    // rebuilt. That is the only case that needs it.
    const size = parseInt($("per-page").value, 10) || 0;
    const index = visible().findIndex(f => f.rel_path === file.rel_path);
    if (size && index >= 0) {
      page = Math.floor(index / size);
      unpin();
    }
    render();
    const area = $("results-scroll");
    if (area) area.scrollTo({top: 0});
  }
  markSelected(file.rel_path);
  selection = new Set([file.rel_path]);
  lastClicked = file.rel_path;
  renderSelectionState();
  showDetails(file.rel_path);
  // The viewer follows the cursor. Left on the old picture, the next key
  // graded the file the cursor had moved to -- one never shown.
  if ($("full-modal").classList.contains("open") && fullFile &&
      fullFile.rel_path !== file.rel_path) {
    viewFullSize(lookup(file.rel_path) || file);
  }
  const tile = document.querySelector(`[data-pick="${CSS.escape(file.rel_path)}"]`);
  if (tile) tile.scrollIntoView({block: "nearest"});
}

function advanceSelection() {
  // Kept for the paths that still ask to move on without having worked out
  // where to go first.
  moveTo(nextAfter(null));
}

function afterBulkGrade() {
  // A held page is meant to stop tiles shifting while an examiner works
  // down it one at a time. Grading the whole page at once is the opposite
  // situation: the work on these files is finished, and holding them means
  // staring at files that no longer match the filter.
  //
  // So: if nothing on the held page still matches, move to the files that
  // follow. If some of it still matches -- a selection that covered only
  // part of the page -- hold it, because the rest is still to be graded.
  if (!pinned || !pinned.length) {
    render();
    return;
  }
  if (paged) {
    // Which held files the filter still shows is the server's to say.
    galleryQuery({paths: pinned, limit: 1}).then(d => {
      if ((d.matching || []).length) { render(); return; }
      pendingAfter = pinned;
      unpin();
      render();
      const area = $("results-scroll");
      if (area) area.scrollTo({top: 0});
    }).catch(e => { $("err").textContent = e.message; });
    return;
  }
  const matching = new Set(visible().map(f => f.rel_path));
  const left = pinned.filter(path => matching.has(path));
  if (left.length) {
    render();
    return;
  }
  page = pageAfterPinned();
  unpin();
  render();
  const area = $("results-scroll");
  if (area) area.scrollTo({top: 0});
}

function pageAfterPinned() {
  // Leaving a held page is not simply "page + 1". Grading its files may have
  // dropped them out of the filter, so the list beneath has shrunk and the
  // next batch has moved up. The right page is the one holding the first
  // file that still matches and comes after the last one on this page.
  const size = parseInt($("per-page").value, 10) || 0;
  if (!pinned || !pinned.length || !size) return page + 1;

  const order = new Map(files.map((f, i) => [f.rel_path, i]));
  const lastSeen = Math.max(...pinned.map(p => order.has(p) ? order.get(p) : -1));
  const list = visible();
  const index = list.findIndex(f => (order.get(f.rel_path) ?? -1) > lastSeen);
  if (index < 0) return Math.max(0, Math.ceil(list.length / size) - 1);
  return Math.floor(index / size);
}

// However many tiles are asked for, this is as many as will be built at
// once. "All" is a reasonable thing to want of two hundred files and a way
// to hang the browser on a hundred and sixty thousand: every tile is an
// image element the browser has to lay out and fetch.
const MAX_TILES = 5000;

function pageOf(list) {
  // The server already sent one page.
  if (paged) return list;
  let size = parseInt($("per-page").value, 10) || 0;
  if ((!size || size > MAX_TILES) && list.length > MAX_TILES) {
    size = MAX_TILES;
    const note = $("page-capped");
    if (note) {
      note.textContent = `showing ${MAX_TILES.toLocaleString()} at a time: ` +
        `more than that in one page is what hangs a browser`;
      note.style.display = "inline";
    }
  } else {
    const note = $("page-capped");
    if (note) note.style.display = "none";
  }
  if (!size || list.length <= size) {
    // Everything fits: there is no page to leave, so nothing to pin.
    page = 0;
    unpin();
    return list;
  }
  const pages = Math.ceil(list.length / size);
  if (page >= pages) {
    // The filter shrank past this page; the view is about to show different
    // files, so start at the top of them.
    page = pages - 1;
    unpin();
    const area = $("results-scroll");
    if (area) area.scrollTo({top: 0});
  }

  const keepPage = $("hold-page") ? $("hold-page").checked : true;
  if (!keepPage) {
    unpin();
    return list.slice(page * size, (page + 1) * size);
  }

  const key = pageSignature(files.length);
  if (pinned && key === pinKey) {
    // Still the same page: show what it was built with, minus anything that
    // has genuinely gone (a new run, a reopened case).
    const byPath = new Map(files.map(f => [f.rel_path, f]));
    const kept = pinned.map(path => byPath.get(path)).filter(Boolean);
    if (kept.length) return kept;
    unpin();
  }
  let slice = list.slice(page * size, (page + 1) * size);

  // A page past the end shows nothing, and during a run that is what
  // happens: the page stays where the examiner left it while the list
  // grows, until a filter or a reopened case leaves the index beyond
  // what is there. An empty gallery beside a count of a hundred
  // thousand files is the result, and it reads as "the program found
  // nothing" rather than "you are on page nine of two".
  //
  // So an index past the end comes back to the last page that has
  // something on it, rather than showing an empty one.
  if (!slice.length && list.length) {
    page = Math.max(0, Math.ceil(list.length / size) - 1);
    slice = list.slice(page * size, (page + 1) * size);
    renderPager(list.length);
  }

  pinned = slice.map(f => f.rel_path);
  pinKey = key;
  return slice;
}

function renderPager(total) {
  // A paged case is never sent whole, so "all" there is pages of MAX_TILES
  // and needs the pager like any other size.
  const size = paged ? pageSize() : (parseInt($("per-page").value, 10) || 0);
  if (!size || total <= size) { $("pager").style.display = "none"; return; }
  const pages = Math.ceil(total / size);
  const from = page * size + 1;
  const to = Math.min(total, (page + 1) * size);
  $("pager").style.display = "flex";
  $("pager").innerHTML =
    `<button class="secondary" data-page="0" ${page === 0 ? "disabled" : ""}>« First</button>` +
    `<button class="secondary" data-page="${page - 1}" ${page === 0 ? "disabled" : ""}>‹ Prev</button>` +
    `<span>${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}` +
    ` &middot; page ${page + 1} of ${pages.toLocaleString()}</span>` +
    `<button class="secondary" data-page="${page + 1}" ${page >= pages - 1 ? "disabled" : ""}>Next ›</button>` +
    `<button class="secondary" data-page="${pages - 1}" ${page >= pages - 1 ? "disabled" : ""}>Last »</button>`;
  $("pager").querySelectorAll("[data-page]").forEach(b =>
    b.addEventListener("click", () => {
      const wanted = parseInt(b.dataset.page, 10);
      // "Next" from a held page means the files after it, which is not
      // necessarily the next page number once some have been graded away.
      page = (wanted === page + 1) ? pageAfterPinned() : Math.max(0, wanted);
      unpin();
      render();
      // Back to the first tile of the new page, inside the scrolling area
      // rather than by moving the whole window.
      $("results-scroll").scrollTo({top: 0, behavior: "smooth"});
    }));
}

// A search is a plain substring match over the fields an examiner would
// recognize a file by, with an optional field prefix for when a bare term
// would match too much. Hashes are matched on any prefix, so the first eight
// characters from a report find the file.
function matchesSearch(f, query) {
  if (!query) return true;
  const fields = {
    name: (f.name || "").toLowerCase(),
    path: (f.source_path || "").toLowerCase(),
    md5: (f.md5 || "").toLowerCase(),
    sha1: (f.sha1 || "").toLowerCase(),
    sha256: (f.sha256 || "").toLowerCase(),
    cam: [(f.camera_make || ""), (f.camera_model || "")].join(" ").toLowerCase(),
    note: ((decisions[f.sha256] || {}).note || "").toLowerCase(),
  };
  return query.split(/\s+/).filter(Boolean).every(term => {
    const colon = term.indexOf(":");
    if (colon > 0) {
      const field = term.slice(0, colon);
      const value = term.slice(colon + 1);
      if (fields[field] !== undefined) return fields[field].indexOf(value) >= 0;
    }
    return Object.values(fields).some(v => v.indexOf(term) >= 0);
  });
}

// Two things the gallery leaves out by default, both for the same reason:
// they are in the case and in the reports, but they are not pictures an
// examiner has to look at.
let hideKnownSoftware = false;
let showContainers = false;
// One tile per group of files that look alike, instead of one per file. The
// tile stands for the group, and grading it grades every file in it --
// anything else would leave members of a stack ungraded behind a tile that
// looks dealt with.
let stackSimilar = false;

function stackMembers(file) {
  if (!file || !file.similar_group) return [file ? file.sha256 : ""];
  return files.filter(f => f.similar_group === file.similar_group)
              .map(f => f.sha256);
}

function collapseStacks(list) {
  if (!stackSimilar) return list;
  const seen = new Set();
  const out = [];
  for (const file of list) {
    if (!file.similar_group) { out.push(file); continue; }
    if (seen.has(file.similar_group)) continue;
    seen.add(file.similar_group);
    out.push(file);
  }
  return out;
}

function isKnownSoftware(f) {
  const sets = (f.hash_sets || []);
  return sets.some(hit => (hit.kind || "") === "nsrl");
}

$("chip-stack").addEventListener("click", () => {
  stackSimilar = !stackSimilar;
  $("chip-stack").classList.toggle("active", stackSimilar);
  page = 0; unpin(); setSelection([]); render();
  const grouped = paged ? ((pv.counts || {}).groups || 0)
    : new Set(files.filter(f => f.similar_group).map(f => f.similar_group)).size;
  toast(stackSimilar
    ? (grouped
       ? `One tile per group of look-alikes: ${grouped.toLocaleString()} ` +
         `group(s). Grading a stacked tile grades every file in it.`
       : `Nothing is grouped yet — run Similar images on the Case Data tab ` +
         `first.`)
    : `Every file has its own tile again.`);
});

$("chip-nsrl").addEventListener("click", () => {
  hideKnownSoftware = !hideKnownSoftware;
  $("chip-nsrl").classList.toggle("active", hideKnownSoftware);
  page = 0; unpin(); render();
  // What a toggle did is a number changing somewhere off screen, so it
  // says so: "something happened and I am not sure what" is not a state
  // to leave an examiner in.
  const hidden = paged ? ((pv.counts || {}).known_software || 0)
    : files.filter(f => isKnownSoftware(f)).length;
  toast(hideKnownSoftware
    ? `Hiding ${hidden.toLocaleString()} file(s) that a known-good set (the ` +
      `NSRL) recognizes as shipped software. They are still in the case and ` +
      `in reports.`
    : `Showing files recognized as known software again.`);
});
$("chip-containers").addEventListener("click", () => {
  showContainers = !showContainers;
  const boxes = paged ? ((pv.counts || {}).containers || 0)
    : files.filter(f => f.container).length;
  toast(showContainers
    ? `Showing ${boxes.toLocaleString()} container(s) — the zips, archives ` +
      `and databases other files came out of. Their contents are listed ` +
      `separately.`
    : `Hiding containers: the files that came out of them are still shown.`);
  $("chip-containers").classList.toggle("active", showContainers);
  page = 0; unpin(); render();
});

function passesBase(f) {
  if (f.container && !showContainers) return false;
  if (hideKnownSoftware && isKnownSoftware(f)) return false;
  // Everything except category, tag and facet: the chips and the search box.
  const query = ($("search").value || "").trim().toLowerCase();
  if (!matchesSearch(f, query)) return false;
  return filter === "all" ? true :
    filter === "deleted" ? f.deleted :
    filter === "carve" ? f.engine === "carve" :
    filter === "database" ? f.engine === "database" :
    filter === "folder" ? f.engine === "folder" :
    filter === "embedded" ? f.engine === "embedded" :
    filter === "gps" ? f.latitude !== null && f.latitude !== undefined :
    filter === "auto" ? !!(decisions[f.sha256] || {}).auto :
    // A hash set's category is a lead, not a review: this is everything a
    // person has still not looked at.
    filter === "needs-review" ? (!decisions[f.sha256] ||
        decisions[f.sha256].auto ||
        decisions[f.sha256].category === uncategorizedCode) :
    f.category === filter;
}

function passesFacets(f, except) {
  // Values within one facet are alternatives; facets are cumulative.
  for (const [key, set] of Object.entries(facetFilter)) {
    if (key === except || !set.size) continue;
    const facet = FACETS.find(x => x.key === key);
    if (!facet) continue;
    const values = facetValues(facet, f);
    const matched = values.length
      ? values.some(v => set.has(v))
      : set.has("\u0000");        // the file carries nothing for this facet
    if (!matched) return false;
  }
  return true;
}

function passesClassification(f) {
  if (similarOnly && !similarOnly.has(f.rel_path)) return false;
  if (catFilter !== "" && String(categoryOf(f)) !== catFilter) return false;
  if (flagFilter.size) {
    const carried = (decisions[f.sha256] || {}).flags || [];
    for (const id of flagFilter) if (carried.indexOf(id) < 0) return false;
  }
  return true;
}

function passesAll(f, exceptFacet) {
  return passesBase(f) && passesClassification(f) && passesFacets(f, exceptFacet);
}

function visible() {
  // A paged case's visible list is the page the server sent: the whole list
  // is never here. Everything that walks visible() therefore walks the page.
  if (paged) return pv.files;
  return collapseStacks(files.filter(f => passesAll(f)));
}

// ---- a case browsed a page at a time ------------------------------------
//
// Past PAGED_THRESHOLD files (media_gui.py) the server does not send the
// case: the page asks /api/gallery/query for the page it shows, the counts
// around it, and -- when an action needs them -- every file a filter
// matches or a look-alike group holds. 46 MB of JSON for 55,508 files was
// the cost of holding it, and a case the size an examiner reviews in
// Griffeye would not have opened at all. The filter is the server's own
// port of this page's (galleryquery.py), held to it by
// tests/test_gallery_parity.py, so a case filters the same either way.
let paged = false, caseTotal = 0;
let pv = {files: [], total: 0, offset: 0, counts: null, facets: null};
// Every file a page has shown, by path: what details, the context menu and
// a selection look a file up in. Capped, oldest first, so browsing a
// million files does not rebuild the list it replaced.
const seen = new Map();
const SEEN_CAP = 50000;
// path -> SHA-256 for everything selectable: what a selection is graded by,
// for files no page has shown (Select all matching).
const knownDigest = new Map();
let pvSeq = 0, pendingAfter = null;

function caseSize() { return paged ? caseTotal : files.length; }

function lookup(path) {
  return paged ? seen.get(path) : files.find(f => f.rel_path === path);
}

function lookupDigest(sha256) {
  if (!paged) return files.find(f => f.sha256 === sha256);
  for (const f of seen.values()) if (f.sha256 === sha256) return f;
  return null;
}

function rememberFiles(list) {
  for (const f of list || []) {
    seen.delete(f.rel_path);
    seen.set(f.rel_path, f);
    knownDigest.set(f.rel_path, f.sha256);
  }
  while (seen.size > SEEN_CAP) seen.delete(seen.keys().next().value);
}

function enterPaged(total) {
  if (!paged) { seen.clear(); pv = {files: [], total: 0, offset: 0, counts: null, facets: null}; }
  paged = true;
  caseTotal = total || 0;
  files = [];
}

function leavePaged() {
  paged = false;
  caseTotal = 0;
  seen.clear();
  knownDigest.clear();
  pv = {files: [], total: 0, offset: 0, counts: null, facets: null};
}

function filterState() {
  // The filter as galleryquery.State reads it -- the same names the parity
  // test sends.
  return {
    filter, catFilter, flagFilter: [...flagFilter],
    facetFilter: Object.fromEntries(Object.entries(facetFilter)
                   .map(([k, v]) => [k, [...v]])),
    search: ($("search").value || "").trim(),
    showContainers, hideKnownSoftware, stackSimilar,
    similarOnly: similarOnly ? [...similarOnly] : null,
    timeZone,
  };
}

async function galleryQuery(extra) {
  const d = await api("/api/gallery/query", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(Object.assign({job: jobId, state: filterState()}, extra || {}))});
  if (d.files) rememberFiles(d.files);
  return d;
}

function pageSize() {
  const size = parseInt($("per-page").value, 10) || 0;
  return (!size || size > MAX_TILES) ? MAX_TILES : size;
}

async function digestsFor(paths) {
  // The SHA-256 of each path, asking the server for any no page has shown
  // (a selection made by Find similar, say).
  const out = new Set(), missing = [];
  for (const path of paths) {
    const digest = knownDigest.get(path);
    if (digest) out.add(digest); else missing.push(path);
  }
  for (let i = 0; i < missing.length; i += 5000) {
    const d = await galleryQuery({paths: missing.slice(i, i + 5000), limit: 1});
    (d.files || []).forEach(f => out.add(f.sha256));
  }
  return [...out];
}

async function renderPaged() {
  const seq = ++pvSeq;
  const size = pageSize();
  // Facets are asked for after the page is drawn: on a very large case they
  // take seconds, and the gallery should not wait for the Filters pane.
  const body = {offset: page * size, limit: size, counts: true};
  const keepPage = $("hold-page") ? $("hold-page").checked : true;
  const key = pageSignature(caseTotal);
  if (pendingAfter) {
    body.after = pendingAfter;
    pendingAfter = null;
  } else if (keepPage && pinned && key === pinKey) {
    body.paths = pinned;          // the held page, as its files stand now
  }
  let d;
  try {
    d = await galleryQuery(body);
  } catch (e) { $("err").textContent = e.message; return; }
  if (seq !== pvSeq) return;      // a later request has already drawn
  if (d.counts) caseTotal = d.counts.case_total;
  pv = {files: d.files || [], total: d.total || 0, offset: d.offset || 0,
        counts: d.counts || pv.counts, facets: d.facets || null};
  page = Math.floor((d.offset || 0) / size);
  if (!d.held) {
    pinned = keepPage && pv.files.length ? pv.files.map(f => f.rel_path) : null;
    pinKey = pageSignature(caseTotal);
  } else if (!pv.files.length) {
    unpin();
  }
  const note = $("page-capped");
  if (note) note.style.display = "none";
  drawGallery(pv.files, pv.total);
  if (pane === "filters") renderFilters();     // "Counting…", then the counts
  if (pane === "classify") renderClassification();
}

// After grading, the counts around the gallery change; in a paged case
// they are the server's, so they are asked for again -- once, however many
// keys were pressed in the meantime.
let countsTimer = null;
function refreshCounts() {
  if (!paged) return;
  clearTimeout(countsTimer);
  countsTimer = setTimeout(async () => {
    try {
      const d = await galleryQuery({limit: 1, offset: 0, counts: true,
                                    facets: pane === "filters"});
      pv.counts = d.counts || pv.counts;
      if (d.facets) pv.facets = d.facets;
      renderCategoryFilter();
      if (pane === "classify") renderClassification();
      if (pane === "filters") renderFilters();
    } catch (e) { /* the counts catch up on the next page */ }
  }, 400);
}

async function desktopOpen(relPath, reveal) {
  try {
    await api("/api/open-file", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, file: relPath, reveal: reveal})
    });
  } catch (e) {
    $("err").textContent = e.message;
  }
}

async function wireFilmstrip(container, relPath, ext) {
  // A browser shows nothing for most AVI and some MOV codecs. Rather than
  // leave a black rectangle, ask the server to decode a few frames -- which
  // it only does for formats the browser cannot play, and caches.
  const strip = container.querySelector("#filmstrip");
  const video = container.querySelector("video[data-film]");
  if (!strip || !video) return;

  const playable = ["mp4", "m4v", "mov", "webm", "ogg", "ogv"]
    .indexOf((ext || "").toLowerCase()) >= 0;
  let decided = false;
  const load = async () => {
    if (decided) return;
    decided = true;
    try {
      const d = await api(`/api/frames?job=${jobId}&file=${encodeURIComponent(relPath)}`);
      if (!d.available) {
        strip.innerHTML = `<span class="hint">${esc(d.message)}</span>`;
        return;
      }
      if (!d.frames.length) {
        strip.innerHTML = `<span class="hint">No frame could be decoded` +
          `${d.error ? ` — ${esc(d.error)}` : ""}. Open with… hands it to a player.</span>`;
        return;
      }
      strip.innerHTML = d.frames.map((src, i) =>
        `<img src="${src}" alt="frame ${i + 1}" title="frame ${i + 1}">`).join("");
    } catch (e) { strip.innerHTML = `<span class="hint">${esc(e.message)}</span>`; }
  };

  // An unplayable format fails to load its metadata; a playable one does not
  // need frames at all.
  if (!playable) { strip.innerHTML = '<span class="hint">decoding frames…</span>'; load(); }
  video.addEventListener("error", () => {
    strip.innerHTML = '<span class="hint">decoding frames…</span>';
    load();
  });
}

// ---- frames fetched ahead of the cursor --------------------------------
// The first hover over a clip waits while its frames decode. Decoding them
// in advance removes that wait, but only if it is done carefully: a page of
// five hundred tiles asking at once would occupy every thread on the server
// and make grading -- the thing an examiner is actually doing -- wait behind
// video nobody has looked at yet.
//
// So: only tiles actually on screen, two at a time, in the order they
// appear, stopping the moment the page changes.
const framesReady = new Map();      // rel_path -> frames, or [] if none
const framesWanted = [];
let framesBusy = 0;
let framesGeneration = 0;
const FRAME_WORKERS = 2;

function prefetchFrames(relPath, generation) {
  if (!relPath || framesReady.has(relPath)) return;
  framesReady.set(relPath, null);   // claimed, so nothing asks twice
  framesWanted.push({relPath, generation});
  pumpFrames();
}

async function pumpFrames() {
  while (framesBusy < FRAME_WORKERS && framesWanted.length) {
    const next = framesWanted.shift();
    if (next.generation !== framesGeneration) {
      framesReady.delete(next.relPath);   // a stale page; let it be asked again
      continue;
    }
    framesBusy++;
    (async () => {
      try {
        const d = await api(`/api/frames?job=${jobId}` +
                            `&file=${encodeURIComponent(next.relPath)}`);
        framesReady.set(next.relPath, (d && d.frames) ? d.frames : []);
      } catch (e) {
        framesReady.set(next.relPath, []);
      } finally {
        framesBusy--;
        pumpFrames();
      }
    })();
  }
}

function watchForFrames(container) {
  // Asked for only once a tile is actually on screen. Scrolling past a
  // hundred videos quickly should not queue a hundred decodes.
  framesGeneration++;
  const generation = framesGeneration;
  const videos = [...container.querySelectorAll("video[data-scrub]")];
  if (!videos.length) return;

  if (typeof IntersectionObserver !== "function") {
    videos.slice(0, 12).forEach(v => prefetchFrames(v.dataset.scrub, generation));
    return;
  }
  const watcher = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      watcher.unobserve(entry.target);
      prefetchFrames(entry.target.dataset.scrub, generation);
    });
  }, {root: $("results-scroll") || null, rootMargin: "200px"});
  videos.forEach(video => watcher.observe(video));
}

function wireTileScrub(tile, poster, relPath) {
  // Frames first, then the video. The frames are already fetched, so the
  // first movement shows something immediately; the video element is made
  // in the background and, once it can decode, takes over and scrubs to any
  // position rather than the dozen the frames cover.
  let frames = null;
  let video = null;
  let videoReady = false;
  let videoFailed = false;
  let seeking = false;
  let pending = null;
  let queued = false;
  let across = 0;

  const setPoster = () => {
    if (frames && frames.length && !poster.getAttribute("src")) {
      poster.setAttribute("src", frames[0]);
    }
  };

  const ensureFrames = async () => {
    if (frames) return;
    const ready = framesReady.get(relPath);
    if (Array.isArray(ready)) { frames = ready; setPoster(); return; }
    try {
      const d = await api(`/api/frames?job=${jobId}` +
                          `&file=${encodeURIComponent(relPath)}`);
      frames = (d && d.frames) ? d.frames : [];
      framesReady.set(relPath, frames);
      setPoster();
    } catch (e) { frames = []; }
  };

  const ensureVideo = () => {
    if (video || videoFailed) return;
    const file = lookup(relPath);
    if (!file) return;
    video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.className = "hovervideo";
    video.addEventListener("loadedmetadata", () => {
      videoReady = isFinite(video.duration) && video.duration > 0;
    });
    // A codec the browser cannot decode is not a failure: the frames came
    // from this program's own decoder and still work.
    video.addEventListener("error", () => {
      videoFailed = true;
      if (video) { video.remove(); video = null; }
    });
    video.addEventListener("seeked", () => {
      seeking = false;
      if (pending !== null) { const next = pending; pending = null; seek(next); }
    });
    video.src = url(file);
    tile.appendChild(video);
  };

  const seek = (seconds) => {
    if (!video || seeking) { pending = seconds; return; }
    seeking = true;
    try { video.currentTime = seconds; } catch (e) { seeking = false; }
  };

  const release = () => {
    // Let the element go rather than leaving it holding a decoder.
    if (video) {
      try { video.pause(); video.removeAttribute("src"); video.load(); }
      catch (e) { /* it is going away regardless */ }
      video.remove();
      video = null;
    }
    videoReady = false;
    seeking = false;
    pending = null;
  };

  tile.addEventListener("mouseenter", () => {
    tile.classList.add("scrubbing");
    ensureFrames();
    ensureVideo();
  });

  tile.addEventListener("mousemove", e => {
    const box = tile.getBoundingClientRect();
    across = Math.min(Math.max((e.clientX - box.left) / box.width, 0), 1);
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (video && videoReady) {
        // Precise: any position in the clip, not just the frames taken.
        video.style.display = "block";
        seek(Math.min(video.duration * 0.995,
                      Math.max(0.05, across * video.duration)));
        return;
      }
      if (frames && frames.length) {
        const at = Math.min(frames.length - 1,
                            Math.floor(across * frames.length));
        if (poster.getAttribute("src") !== frames[at]) {
          poster.setAttribute("src", frames[at]);
        }
      }
    });
  });

  tile.addEventListener("mouseleave", () => {
    tile.classList.remove("scrubbing");
    release();
    if (frames && frames.length) poster.setAttribute("src", frames[0]);
  });
}

function wireScrub(container) {
  // Scrubbing shows a frame at the position under the cursor, which is what
  // tells an examiner whether a clip matters without playing it.
  //
  // It used to do that by setting the video's currentTime on every mouse
  // move: dozens of seeks a second, each one a range request to the server
  // and a decode in the browser. On carved video -- malformed by nature,
  // since a carve rarely recovers a clean container -- Chrome falls behind,
  // the queue grows, and the tab stops responding.
  //
  // So the video is not seeked at all. The frames the server already
  // extracts are fetched once per clip and swapped under the cursor, which
  // is an image change and nothing more. Seeking remains as the fallback
  // for a clip with no frames, throttled and never overlapping.
  // Gallery tiles: a picture, with a video made on hover and released after.
  container.querySelectorAll("[data-poster]").forEach(poster => {
    const tile = poster.parentElement;
    if (!tile || tile.dataset.scrubWired) return;
    tile.dataset.scrubWired = "1";
    wireTileScrub(tile, poster, poster.dataset.poster || "");
  });

  // The details pane keeps a real <video> -- there is one of them, it has
  // controls, and an examiner is looking at that file deliberately.
  container.querySelectorAll("video[data-scrub]").forEach(video => {
    const tile = video.parentElement;
    if (!tile || tile.dataset.scrubWired) return;
    tile.dataset.scrubWired = "1";

    const relPath = video.dataset.scrub || "";
    let frames = null;          // null = not asked yet, [] = none to be had
    let shot = null;            // the <img> laid over the video while scrubbing
    let asking = false;

    const ensureFrames = async () => {
      if (frames !== null || asking) return;
      const ready = framesReady.get(relPath);
      if (Array.isArray(ready)) { frames = ready; return; }
      asking = true;
      try {
        const d = await api(`/api/frames?job=${jobId}` +
                            `&file=${encodeURIComponent(relPath)}`);
        frames = (d && d.frames) ? d.frames : [];
        framesReady.set(relPath, frames);
      } catch (e) { frames = []; }
      finally { asking = false; }
    };

    const show = (index) => {
      if (!frames || !frames.length) return false;
      const at = Math.min(frames.length - 1, Math.max(0, index));
      if (!shot) {
        shot = document.createElement("img");
        shot.className = "scrubshot";
        shot.alt = "";
        tile.appendChild(shot);
      }
      const wanted = frames[at];
      if (shot.getAttribute("src") !== wanted) shot.setAttribute("src", wanted);
      shot.style.display = "block";
      return true;
    };

    // The fallback: seek, but only one at a time and never faster than the
    // decoder can keep up with.
    let seeking = false;
    let pending = null;
    const seekTo = (seconds) => {
      if (seeking) { pending = seconds; return; }
      seeking = true;
      video.currentTime = seconds;
    };
    video.addEventListener("seeked", () => {
      seeking = false;
      if (pending !== null) {
        const next = pending;
        pending = null;
        seekTo(next);
      }
    });
    video.addEventListener("error", () => { seeking = false; pending = null; });

    let ready = false;
    video.addEventListener("loadedmetadata", () => { ready = true; });

    let queued = false;
    let lastAcross = 0;
    video.addEventListener("mousemove", e => {
      const box = video.getBoundingClientRect();
      lastAcross = Math.min(Math.max((e.clientX - box.left) / box.width, 0), 1);
      tile.classList.add("scrubbing");
      if (queued) return;
      // One update per frame the browser draws, not one per mouse event.
      queued = true;
      requestAnimationFrame(async () => {
        queued = false;
        await ensureFrames();
        if (show(Math.round(lastAcross * ((frames || []).length - 1)))) return;
        if (!ready || !isFinite(video.duration) || video.duration <= 0) return;
        seekTo(Math.min(video.duration * 0.995,
                        Math.max(0.05, lastAcross * video.duration)));
      });
    });

    video.addEventListener("mouseleave", () => {
      tile.classList.remove("scrubbing");
      if (shot) shot.style.display = "none";
      pending = null;
      if (ready && isFinite(video.duration) && !seeking) {
        try { video.currentTime = 0.05; } catch (e) { /* nothing to do */ }
      }
    });
  });
}

function wireDetails(container) {
  container.querySelectorAll("[data-details]").forEach(el => {
    el.addEventListener("click", e => {
      e.preventDefault();
      markSelected(el.dataset.details);
      pickFile(el.dataset.details, e);
    });
    // Double-click opens the file full size, as it does in every gallery.
    el.addEventListener("dblclick", e => {
      e.preventDefault();
      const file = lookup(el.dataset.details);
      if (file && file.category !== "other") viewFullSize(file);
    });
  });
}

function stackBadges(f) {
  // What a tile stands for besides itself. Two different things, so two
  // badges: identical files, and files that merely look alike. Collapsing
  // them into one number would hide which is which, and the difference
  // decides how much a count means.
  const copies = f.copies || 1;
  const alike = f.similar_count || 0;
  const bits = [];
  if (alike > 1) {
    bits.push(`<span class="stackn alike" title="${alike} file(s) look like ` +
              `this one">\u2248 ${alike}</span>`);
  }
  if (copies > 1) {
    bits.push(`<span class="stackn" title="${copies} identical copies of ` +
              `this file were found">\u29C9 ${copies}</span>`);
  }
  return bits.length ? `<div class="stacks">${bits.join("")}</div>` : "";
}

function flagTags(f) {
  // Flags on the tile, so a page of decisions reads without opening each one.
  const decided = decisions[f.sha256];
  if (!decided || !decided.flags || !decided.flags.length) return "";
  return `<div class="tileflags">` + decided.flags.map(id => {
    const flag = caseFlags.find(x => x.id === id);
    return flag ? `<span class="tag flag" style="--cat:${esc(flag.color)}">` +
                  `${esc(flag.name)}</span>` : "";
  }).join("") + `</div>`;
}

function markSelected(relPath) {
  document.querySelectorAll(".card.selected, tr.selected").forEach(el =>
    el.classList.remove("selected"));
  const target = document.querySelector(`[data-details="${CSS.escape(relPath)}"]`);
  if (!target) return;
  const row = target.closest(".card") || target.closest("tr");
  if (row) row.classList.add("selected");
}

function wireFileActions(container) {
  container.querySelectorAll("[data-open]").forEach(b =>
    b.addEventListener("click", e => {
      e.preventDefault();
      desktopOpen(b.dataset.open, false);
    }));
  container.querySelectorAll("[data-reveal]").forEach(b =>
    b.addEventListener("click", e => {
      e.preventDefault();
      desktopOpen(b.dataset.reveal, true);
    }));
  wireSaves(container);
}

// Save puts a copy of evidence wherever this browser downloads to. One click
// did that with no word (v2026.11.320, U12): it asks, and points at Export
// files, which hashes and logs every copy it writes.
function wireSaves(container) {
  container.querySelectorAll("a[data-save]").forEach(link => {
    if (link.dataset.saveWired) return;
    link.dataset.saveWired = "1";
    link.addEventListener("click", e => {
      if (!confirm(`Download a copy of ${link.dataset.save} to this browser's ` +
                   `downloads folder?\n\nThe copy is not logged or hash-checked. ` +
                   `For a copy that is, use Report / Export → Export files.`)) {
        e.preventDefault();
      }
    });
  });
}

function url(f, dl) {
  return `/api/file?job=${jobId}&file=${encodeURIComponent(f.rel_path)}` + (dl ? "&download=1" : "");
}

function render() {
  // The ribbon lights its toggles from the same state the gallery draws
  // from, so it is redrawn here rather than at each place that changes
  // something. Clearing the filters used to leave "Hide known software"
  // and "Show containers" lit on a gallery that was showing everything,
  // because the ribbon was never told.
  if (typeof renderRibbon === "function" && !renderingRibbon) {
    renderingRibbon = true;
    try { renderRibbon(); } finally { renderingRibbon = false; }
  }
  if (paged) { renderPaged(); return; }
  const all = visible();
  // Showing fifty thousand tiles at once is what makes a gallery unusable,
  // so only a page of them is built at a time.
  const list = pageOf(all);
  drawGallery(list, all.length);
}

// The page of tiles, the pager, the counts around them, and why a gallery
// is empty. `total` is how many files the filter shows -- the page's own
// count, or the server's when the case is browsed a page at a time.
function drawGallery(list, total) {
  renderPager(total);
  renderCategoryFilter();
  renderActiveFilters(total, caseSize());
  const query = ($("search").value || "").trim();
  $("search-count").textContent = query
    ? `${total.toLocaleString()} of ${caseSize().toLocaleString()} match`
    : "";
  // Three different reasons for an empty gallery, and telling an examiner
  // the wrong one wastes their time: during a run the page is held so
  // arriving files do not shuffle what is being read, which eventually
  // means standing on a page past the end while the count climbs. Saying
  // "no files match this filter" there is simply untrue.
  const held = $("hold-page") && $("hold-page").checked;
  $("empty").style.display = (total && list.length) ? "none" : "block";
  if (!caseSize()) {
    $("empty").textContent = "Nothing extracted yet.";
  } else if (!total) {
    $("empty").textContent = "No files match this filter.";
  } else if (!list.length) {
    $("empty").innerHTML =
      `${count(total)} file(s) match, but this page is empty` +
      (held ? ` — the page is held, so it stays where you were reading ` +
              `while the run adds more. Press First, or untick "Hold the ` +
              `page" to follow the run.`
            : ` — press First.`);
  }
  $("cat-bar").style.display = caseSize() ? "flex" : "none";
  $("gallery").style.display = (view === "gallery" && list.length) ? "grid" : "none";
  $("table").style.display = (view === "table" && list.length) ? "block" : "none";

  if (view === "gallery") {
    $("gallery").innerHTML = list.map(f => {
      let thumb;
      if (f.previewable && f.category === "image") {
        // The gallery asks for a tile-sized copy, not the original. The
        // full picture is loaded when one is actually opened.
        thumb = `<img loading="lazy" alt="${esc(f.name)}" src="/api/thumb?job=${jobId}` +
                `&file=${encodeURIComponent(f.rel_path)}">`;
      } else if (f.convertible) {
        // HEIC off a phone, a TIFF, a camera raw: rendered on request so the
        // tile is the picture rather than a grey rectangle.
        thumb = `<img loading="lazy" alt="${esc(f.name)}" src="/api/preview?job=${jobId}` +
                `&file=${encodeURIComponent(f.rel_path)}">`;
      } else if (f.previewable && f.category === "video") {
        // Scrubbable: moving across the tile seeks the video, so a clip can
        // be judged without opening it. preload="metadata" fetches the
        // header only, so a page of videos does not download itself.
        // No <video> element here. A page of five hundred video tiles was
        // five hundred media elements, each fetching its header and holding
        // decoder resources; browsers cap how many they will keep and start
        // tearing them down, which is a large part of why a page of video
        // became unresponsive. The tile is a picture, and a video element
        // is made only while the cursor is on it -- one at a time, released
        // on the way out.
        thumb = `<img class="poster" alt="${esc(f.name)}" loading="lazy" ` +
                `data-poster="${esc(f.rel_path)}">` +
                `<span class="vidmark">▶</span>` +
                `<span class="scrubhint">hover to scrub</span>`;
      } else {
        thumb = `<span class="ph">.${esc(f.ext)}</span>`;
      }
      const decided = decisions[f.sha256];
      const cat = decided ? categories.find(c => c.code === decided.category) : null;
      const graded = cat && cat.code !== uncategorizedCode;
      const auto = decided && decided.auto;
      return `<div class="card${shouldBlur(f) ? " blurred" : ""}" data-pick="${esc(f.rel_path)}"${graded ? ` style="--cat:${esc(cat.color)}"` : ""}>
        ${graded ? `<div class="catstrip${auto ? " auto" : ""}" ` +
                   `title="${esc(cat.name)}${auto ? " — set by a hash set, " +
                   "not by an examiner" : ""}"></div>` : ""}
        <a class="thumb" href="#" data-details="${esc(f.rel_path)}">${thumb}</a>
        <div class="meta">
          <div class="nm">${esc(f.name)}</div>
          <div class="sub">
            <span>${esc(f.size_human)}</span>
            <span>${f.deleted ? '<span class="tag del">deleted</span> ' : ""}
                  ${f.engine === "carve" ? '<span class="tag carve">carved</span>' : ""}</span>
          </div>
          ${flagTags(f)}
          <div class="acts">
            <button class="secondary tiny" data-open="${esc(f.rel_path)}">Open with…</button>
            <button class="secondary tiny" data-reveal="${esc(f.rel_path)}" title="Opens the folder holding this file, with the file selected">Show in folder</button>
            <a class="tiny" href="${url(f, 1)}" data-save="${esc(f.name)}">Save…</a>
          </div>
        </div></div>`;
    }).join("");
    wireFileActions($("gallery"));
    wireDetails($("gallery"));
    wireScrub($("gallery"));
    watchForFrames($("gallery"));
    wireContextMenu($("gallery"));
    if (selectedPath) markSelected(selectedPath);
    renderSelectionState();
  } else {
    $("rows").innerHTML = list.map(f => `<tr data-pick="${esc(f.rel_path)}">
      <td><a href="#" data-details="${esc(f.rel_path)}">${esc(f.name)}</a></td>
      <td>${esc(f.ext)}</td><td>${esc(f.size_human)}</td>
      <td>${esc(f.engine)}${f.deleted ? ' <span class="tag del">del</span>' : ""}</td>
      <td title="${esc(f.source)}">${esc(f.source.slice(0, 40))}</td>
      <td title="${esc(f.sha256)}">${esc(f.sha256.slice(0, 12))}…</td>
      <td><button class="secondary tiny" data-open="${esc(f.rel_path)}">Open with…</button>
          <button class="secondary tiny" data-reveal="${esc(f.rel_path)}" title="Opens the folder holding this file, with the file selected">Show in folder</button>
          <a href="${url(f, 1)}" data-save="${esc(f.name)}">save…</a></td></tr>`).join("");
    wireFileActions($("rows"));
    wireDetails($("rows"));
    wireContextMenu($("rows"));
    if (selectedPath) markSelected(selectedPath);
    renderSelectionState();
  }
}

// ---- refresh: re-read the run, do not restart it -----------------------
$("btn-refresh").addEventListener("click", async () => {
  if (!jobId) { $("err").textContent = "No run to refresh yet."; return; }
  const chip = $("btn-refresh");
  chip.textContent = "↻ Refreshing…";
  try {
    const d = await api(`/api/status?job_id=${jobId}`);
    files = takeFiles(d, files);
    if (d.summary) { showSummary(d.summary, d.status); showFinished(d.status, d.summary, caseSize()); }
    render();
    if (selectedPath) showDetails(selectedPath);
  } catch (e) {
    $("err").textContent = e.message;
  } finally {
    chip.textContent = "↻ Refresh";
  }
});

// ---- details pane -------------------------------------------------------
let selectedPath = null, detailsOpen = true;

// Files picked for a bulk action, by their path in the output. A review of
// fifty thousand files is mostly "these three hundred are all the same
// thing", so selecting many and grading them once is the normal case, not a
// special one.
let selection = new Set();
let lastClicked = null;

function selectedDigests() {
  if (paged) {
    return [...new Set([...selection].map(p => knownDigest.get(p)).filter(Boolean))];
  }
  const wanted = new Set();
  files.forEach(f => { if (selection.has(f.rel_path)) wanted.add(f.sha256); });
  return [...wanted];
}

function setSelection(paths) {
  selection = new Set(paths);
  renderSelectionState();
}

function renderSelectionState() {
  document.querySelectorAll("[data-pick]").forEach(el =>
    el.classList.toggle("picked", selection.has(el.dataset.pick)));
  const n = selection.size;
  $("sel-count").textContent = n ? `${n.toLocaleString()} selected` : "";
  $("sel-actions").style.display = n ? "inline-flex" : "none";
  // Whether a category key grades one file or three hundred should be
  // visible before the key is pressed, not after.
  const bar = $("details");
  if (bar) {
    const note = bar.querySelector(".bulknote");
    if (note) note.textContent = n > 1
      ? `applies to all ${n.toLocaleString()} selected files` : "";
  }
}

function pickFile(relPath, event) {
  const list = visible();
  const index = list.findIndex(f => f.rel_path === relPath);
  if (event && event.shiftKey && lastClicked !== null) {
    // A range, which is how a run of similar files actually gets chosen.
    const from = list.findIndex(f => f.rel_path === lastClicked);
    if (from >= 0 && index >= 0) {
      const [a, b] = from < index ? [from, index] : [index, from];
      for (let i = a; i <= b; i++) selection.add(list[i].rel_path);
    }
  } else if (event && (event.ctrlKey || event.metaKey)) {
    selection.has(relPath) ? selection.delete(relPath) : selection.add(relPath);
    lastClicked = relPath;
  } else {
    selection = new Set([relPath]);
    lastClicked = relPath;
  }
  renderSelectionState();
  showDetails(relPath);
}

function setDetailsVisible(on) {
  detailsOpen = on;
  $("btn-pane").classList.toggle("active", on);
  ["btn-details", "btn-classify", "btn-filters"].forEach(id =>
    $(id).classList.toggle("active", on && id === "btn-" +
      {details: "details", classify: "classify", filters: "filters"}[pane]));
  $("details-col").style.display = on ? "block" : "none";
  // The column only takes grid space while it is open, so the gallery gets
  // the width back when it closes.
  $("wrap").classList.toggle("with-details", on);

  // The ribbon lights Details, Classification and Filters from this
  // state, so it has to be told. Closing the pane with its own ✕ left all
  // three lit over a pane that was not there, and the only way to clear
  // them was to open the pane again.
  if (typeof renderRibbon === "function" && !renderingRibbon) {
    renderingRibbon = true;
    try { renderRibbon(); } finally { renderingRibbon = false; }
  }
}

$("btn-details").addEventListener("click", () => {
  showPane("details");
  if (!selectedPath) $("details").innerHTML =
    '<p class="hint" style="margin:0;">Click a file to see its details.</p>';
});
$("details-close").addEventListener("click", () => setDetailsVisible(false));
$("btn-details").classList.add("active");

// A locator map that has no basemap behind it answers with an error; the
// image then goes rather than showing as broken. Error events do not bubble,
// hence the capturing listener.
document.addEventListener("error", e => {
  const t = e.target;
  if (t && t.tagName === "IMG" && t.classList.contains("locator")) t.remove();
}, true);

function row(label, value, mono) {
  const shown = (value === null || value === undefined || value === "")
    ? '<span class="none">—</span>' : esc(String(value));
  return `<tr><td class="k">${esc(label)}</td>` +
         `<td class="${mono === false ? "" : "v"}">${shown}</td></tr>`;
}

async function showDetails(relPath) {
  selectedPath = relPath;
  if (pane !== "details") return;   // the pane is showing something else
  if (!detailsOpen) setDetailsVisible(true);
  const box = $("details");
  $("details-col").style.display = "block";
  $("wrap").classList.add("with-details");
  box.innerHTML = '<p class="hint" style="margin:0;">Loading…</p>';
  // A newly opened pane should show the file, not wherever it was scrolled to.
  $("details-col").scrollTop = 0;
  try {
    const d = await api(`/api/details?job=${jobId}&file=${encodeURIComponent(relPath)}`);
    const r = d.record;
    const fileUrl = `/api/file?job=${jobId}&file=${encodeURIComponent(relPath)}`;

    const blur = shouldBlur({category: r.category, sha256: r.sha256}) ? " blurred" : "";
    let preview = '<div class="preview"><span class="hint">no preview for this format — ' +
                  'use Open with…</span></div>';
    if (d.previewable && r.category === "image")
      preview = `<div class="preview${blur}"><div class="maskwrap"><img src="${fileUrl}" alt=""><div class="masks"></div></div></div>`;
    else if (r.category === "image" && d.convertible)
      preview = `<div class="preview${blur}"><div class="maskwrap"><img alt="" src="/api/preview?job=` +
                `${jobId}&file=${encodeURIComponent(relPath)}"><div class="masks"></div></div></div>` +
                `<p class="hint" style="margin:-4px 0 8px;">Rendered for ` +
                `display; the file itself is untouched.</p>`;
    else if (r.category === "video")
      preview = `<div class="preview${blur}"><video src="${fileUrl}" controls ` +
                `preload="metadata" data-scrub="${esc(relPath)}" muted ` +
                `playsinline data-film="${esc(relPath)}"></video></div>` +
                `<div class="filmstrip" id="filmstrip"></div>` +
                `<p class="hint" style="margin:-4px 0 8px;">Move across the ` +
                `picture to scrub; the controls play it.</p>`;

    const copies = d.exact_copies.length
      ? d.exact_copies.map(c =>
          `${esc(c.engine)}: ${esc(c.source_path || ("offset " + c.offset))}`).join("<br>")
      : null;
    const similarList = (d.similar && d.similar.length)
      ? d.similar.map(v =>
          `<a href="#" data-details="${esc(v.out_path)}">` +
          `${esc(v.out_path.split("/").pop())}</a> ` +
          `<span class="none">(${v.distance} bits apart)</span>`).join("<br>")
      : (d.similarity_ran ? "none" : null);
    const sets = d.hash_sets.length
      ? d.hash_sets.map(h => {
          const record = h.record || {};
          // The fields an examiner acts on come first; the rest follow.
          const order = ["MediaID", "Category", "Series", "Tags",
                         "VictimIdentified", "OffenderIdentified",
                         "IsDistributed", "MediaSize", "DateUpdated",
                         "IsPrecategorized", "PrecategorizationSource",
                         "RelativeFilePath", "Description"];
          const keys = order.filter(k => record[k] !== undefined)
            .concat(Object.keys(record).filter(k => order.indexOf(k) < 0));
          const detail = keys.map(k =>
            `<span class="none">${esc(k)}</span> ${esc(record[k])}`).join("<br>");
          return `<strong>${esc(h.set)}</strong> (${esc(h.kind)})` +
                 (h.note ? ` — ${esc(h.note)}` : "") +
                 (detail ? `<div style="margin:3px 0 6px 8px; ` +
                           `border-left:2px solid var(--border); padding-left:8px;">` +
                           `${detail}</div>` : "");
        }).join("<br>")
      : null;

    const decision = d.decision || {category: uncategorizedCode, note: "", flags: []};
    const catButtons = categories.map(c =>
      `<button class="cat ${decision.category === c.code ? "on" : ""}" ` +
      `data-cat="${c.code}" style="--cat:${esc(c.color)}" ` +
      `title="${esc(c.name)}${c.key ? ` — press ${c.key}` : ""}">` +
      `<span class="k">${esc(c.key || c.code)}</span> ${esc(c.name)}</button>`
    ).join("");
    const flagButtons = caseFlags.length
      ? caseFlags.map(f =>
          `<button class="flagbtn ${decision.flags.indexOf(f.id) >= 0 ? "on" : ""}" ` +
          `data-flag="${f.id}" style="--cat:${esc(f.color)}" ` +
          `title="${esc(f.name)}${f.key ? ` — press ${f.key}` : ""}">` +
          `${f.key ? `<span class="k">${esc(f.key)}</span> ` : ""}${esc(f.name)}` +
          `<span class="x" data-delflag="${f.id}" title="Delete this flag ` +
          `from the case">×</span></button>`).join("")
      : `<span class="hint">None in this case — add whatever it needs.</span>`;

    box.innerHTML =
      `<h3>${esc(r.filename || relPath.split("/").pop())}</h3>` +
      `<div class="path">${esc(r.source_path || "")}</div>` +
      preview +
      `<div class="acts" style="display:flex; gap:6px; margin-bottom:10px;">
         <button class="secondary tiny" data-open="${esc(relPath)}">Open with…</button>
         <button class="secondary tiny" data-reveal="${esc(relPath)}">Show in folder</button>
         <a class="tiny" href="${fileUrl}&download=1" data-save="${esc(r.out_path.split("/").pop())}">Save…</a>
         ${r.category === "video" ? `<button class="secondary tiny" data-story="${esc(relPath)}">Storyboard</button>` : ""}
         ${r.category === "image" && r.sha256 ? `<button class="secondary tiny" data-face="${esc(r.sha256)}" title="Order the case by how alike each face is to one in this picture. An investigative lead only.">Others like this face</button>` : ""}
       </div>` +
      `<div class="section">Category` +
      (caseRegion && caseRegion.id !== "US"
        ? `<span class="hint" id="case-region"> (${esc(caseRegion.label)})</span>` : "") +
      `<span class="hint"> — press a key, or click; the cursor moves on</span>` +
      `<span class="hint bulknote" style="display:block; color:var(--accent);"></span>` +
      `</div><div class="catbar">${catButtons}</div>` +
      `<div class="section">Flags` +
      `<span class="hint"> — any number per file, independent of category; ` +
      `yours to add or delete</span>` +
      `</div><div class="flagbar">${flagButtons}` +
      `<button class="flagbtn add" id="flag-new">+ new flag</button></div>` +
      `<div class="section">Notes</div>` +
      `<label class="notelabel">` +
      `<textarea id="file-note" rows="3" placeholder="saves as you type">` +
      `${esc(decision.note || "")}</textarea></label>` +
      "<table>" +
      row("Source", d.source) +
      row("Type", `${r.category} / .${r.ext}`) +
      row("Original name", (r.source_path || "").split(/[\\/]/).pop()) +
      row("File path", r.source_path) +
      row("Stored at", d.stored_at) +
      row("Size", `${Number(r.size).toLocaleString()} bytes`) +
      row("Captured", inZone(r.captured)) +
      row("Camera", [r.camera_make, r.camera_model].filter(Boolean).join(" ")) +
      row("Coordinates", r.latitude === null || r.latitude === undefined ? "" :
          `${r.latitude}, ${r.longitude}` +
          (r.altitude === null || r.altitude === undefined ? "" : ` (${r.altitude} m)`)) +
      (r.latitude === null || r.latitude === undefined ? "" :
        // Drawn here from the offline basemap when one is imported and covers
        // the position; the image removes itself when none does.
        `<tr><td class="k">Map</td><td><img class="locator" alt="" ` +
        // No onerror= here: the page's security policy refuses inline
        // handlers, so the capturing "error" listener below removes it.
        `src="/api/locator?lat=${encodeURIComponent(r.latitude)}&lon=` +
        `${encodeURIComponent(r.longitude)}">` +
        `<a href="https://www.google.com/maps?q=` +
        `${encodeURIComponent(r.latitude)},${encodeURIComponent(r.longitude)}" ` +
        `target="_blank" rel="noreferrer">` +
        `open in a map</a> <span class="none">(leaves this machine)</span></td></tr>`) +
      row("Recovered by", `${r.engine} (${r.signature})`) +
      row("Deleted", r.deleted ? "yes" : "no") +
      row("FS created", inZone(r.crtime || r.ctime)) +
      row("FS written", inZone(r.mtime)) +
      row("FS accessed", inZone(r.atime)) +
      row("Byte offset", r.offset === null ? "" : r.offset) +
      row("MD5", r.md5) + row("SHA-1", r.sha1) + row("SHA-256", r.sha256) +
      // Built from escaped parts, so it goes in as markup: through row() the
      // <br> between locations was shown as the text "<br>" (U11).
      `<tr><td class="k">Exact copies</td><td class="v">` +
        (copies ? `${d.exact_copies.length} other location(s)<br>${copies}` : "none") +
        `</td></tr>` +
      row("Category set by", decision.auto
          ? `${esc(decision.by || "a hash set")} — not an examiner's decision`
          : (decision.by || "")) +
      (d.thumbnail_source
        ? row("Cached thumbnail of",
              `${esc(d.thumbnail_source.path || d.thumbnail_source.name)}` +
              `<br><span class="none">named from the Windows Search index: ` +
              `what the thumbnail was, not whether the file is still there` +
              `</span>`)
        : "") +
      (d.record.skin_band
        ? row("Skin-tone proportion",
              `${esc(d.record.skin_band)}` +
              (d.record.skin !== null && d.record.skin !== undefined
                ? ` (${(d.record.skin * 100).toFixed(0)}% of the frame)` : "") +
              `<br><span class="none">a color measurement, not a judgement ` +
              `about the picture</span>`)
        : "") +
      ((d.from_video || []).length
        ? row("Appears in a video",
              d.from_video.map(hit =>
                `<strong>${esc(hit.name)}</strong> — frame ${hit.frame}, ` +
                `${esc(hit.confidence)} (${hit.distance} bits apart)`).join("<br>") +
              `<br><span class="none">matched on how the picture looks, not ` +
              `on its bytes</span>`)
        : "") +
      ((d.stills_from_this || []).length
        ? row("Stills from this video",
              d.stills_from_this.slice(0, 12).map(hit =>
                `<strong>${esc(hit.name)}</strong> — frame ${hit.frame}, ` +
                `${esc(hit.confidence)}`).join("<br>") +
              (d.stills_from_this.length > 12
                ? `<br><span class="none">and ${d.stills_from_this.length - 12} ` +
                  `more</span>` : ""))
        : "") +
      row("Hash sets", sets || "no match in the loaded sets") +
      (d.credentials ? row("Content Credentials",
        `${esc(d.credentials.generator || "present")}` +
        (d.credentials.source ? ` · ${esc(d.credentials.source)}` : "") +
        (d.credentials.signer
          ? `<br>signed, it states, by ${esc(d.credentials.signer)}` : "") +
        (d.credentials.ai
          ? `<br><span class="warn">This file states it was made by a ` +
            `trained model.</span>` : "") +
        `<br><span class="hint">What the file states about itself. No ` +
        `signature was checked, a manifest can be copied into another file, ` +
        `and a signer here is a name in the file rather than a proven ` +
        `identity.</span>`) : "") +
      row("Similar group", d.similar_group ? `#${d.similar_group}` :
          (d.similarity_ran ? "not in a group"
           : (d.similarity_available ? "not checked yet"
              : "needs Pillow — pip install pillow"))) +
      row("Visually similar", similarList) +
      row("Notes", r.note, false) +
      "</table>";
    wireFileActions(box);
    wireDetails(box);
    wireScrub(box);
    wireFilmstrip(box, relPath, r.ext);
    box.querySelectorAll("[data-story]").forEach(b =>
      b.addEventListener("click", e => { e.preventDefault(); openStoryboard(b.dataset.story); }));
    box.querySelectorAll("[data-face]").forEach(b =>
      b.addEventListener("click", e => { e.preventDefault(); chooseFace(b.dataset.face); }));
    wireReview(box, r.sha256);
    loadMasks(r.sha256).then(list => {
      if (selectedPath === relPath) drawMasks(box, list);
    });
  } catch (e) {
    box.innerHTML = `<p class="hint" style="color:var(--err); margin:0;">${esc(e.message)}</p>`;
  }
}

// ---- tile size, paging and time zone ----------------------------------
// All three are display settings, remembered per browser so a long review
// does not start from the defaults every morning.
function remember(key, value) {
  try { localStorage.setItem("fme." + key, value); } catch (e) { /* private mode */ }
}
function recall(key, fallback) {
  try { const v = localStorage.getItem("fme." + key); return v === null ? fallback : v; }
  catch (e) { return fallback; }
}

let page = 0;

function applyTileSize(px) {
  $("gallery").style.setProperty("--tile", px + "px");
  remember("tile", px);
}
$("tile-size").addEventListener("input", () => applyTileSize($("tile-size").value));
$("tile-size").value = recall("tile", 170);
applyTileSize($("tile-size").value);

$("per-page").addEventListener("change", () => {
  remember("perPage", $("per-page").value);
  page = 0;
  render();
});
$("per-page").value = recall("perPage", "500");

// Time zones: the browser knows them all, including when daylight saving
// applies on any given date, so the list is built from what it supports and
// the conversion is left to it rather than reimplemented here.
const COMMON_ZONES = [
  "UTC",
  "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
  "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu",
  "America/Halifax", "America/Puerto_Rico", "America/Sao_Paulo",
  "Europe/London", "Europe/Dublin", "Europe/Paris", "Europe/Berlin",
  "Europe/Madrid", "Europe/Rome", "Europe/Athens", "Europe/Moscow",
  "Africa/Lagos", "Africa/Johannesburg", "Africa/Cairo",
  "Asia/Jerusalem", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata",
  "Asia/Bangkok", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Tokyo",
  "Asia/Seoul", "Asia/Singapore",
  "Australia/Perth", "Australia/Adelaide", "Australia/Brisbane",
  "Australia/Sydney", "Pacific/Auckland",
];
const LOCAL_ZONE = (Intl.DateTimeFormat().resolvedOptions().timeZone) || "UTC";
let timeZone = recall("tz", LOCAL_ZONE);

function zoneLabel(zone, when) {
  // The abbreviation moves with the date: EST in January, EDT in July.
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, timeZoneName: "short"}).formatToParts(when || new Date());
    const found = parts.find(p => p.type === "timeZoneName");
    return found ? found.value : zone;
  } catch (e) { return zone; }
}

(function buildZones() {
  const zones = COMMON_ZONES.slice();
  if (zones.indexOf(LOCAL_ZONE) < 0) zones.unshift(LOCAL_ZONE);
  $("tz-select").innerHTML = zones.map(z =>
    `<option value="${esc(z)}"${z === timeZone ? " selected" : ""}>` +
    `${esc(z === LOCAL_ZONE ? z + " (this machine)" : z)}</option>`).join("");
  $("tz-select").addEventListener("change", () => {
    timeZone = $("tz-select").value;
    remember("tz", timeZone);
    render();
    if (selectedPath) showDetails(selectedPath);
  });
})();

function inZone(value) {
  // Only timestamps that carry an offset can be converted. A file system
  // time is recorded in UTC and converts safely; an EXIF capture time is
  // wall-clock as the camera recorded it, with no zone at all, so it is
  // shown as-is and labeled rather than silently shifted.
  if (!value) return "";
  const text = String(value);
  const zoned = /[zZ]$|[+-]\d\d:?\d\d$/.test(text);
  if (!zoned) return text.replace("T", " ").slice(0, 19) + " (as recorded)";
  const when = new Date(text);
  if (isNaN(when.getTime())) return text;
  try {
    const formatted = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(when).replace(",", "");
    return `${formatted} ${zoneLabel(timeZone, when)}`;
  } catch (e) { return text; }
}

// ---- right-click menu --------------------------------------------------
// Everything that can be done to a file, where the file is. A review is
// thousands of small decisions, and crossing the screen for each one is the
// difference between an hour and an afternoon.
let ctxFile = null;

function closeMenu() { $("ctx").style.display = "none"; }
document.addEventListener("click", closeMenu);
document.addEventListener("scroll", closeMenu, true);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });

function menuItem(label, action, extra) {
  const item = document.createElement("div");
  item.className = "item" + (extra && extra.disabled ? " off" : "") +
                   (extra && extra.on ? " on" : "");
  if (extra && extra.color) item.style.setProperty("--cat", extra.color);
  item.innerHTML = (extra && extra.swatch ? '<span class="swatch"></span>' : "") +
    `<span>${esc(label)}</span>` +
    (extra && extra.key ? `<span class="k">${esc(extra.key)}</span>` : "");
  if (!(extra && extra.disabled))
    item.addEventListener("click", e => { e.stopPropagation(); closeMenu(); action(); });
  return item;
}

function openMenu(file, x, y) {
  ctxFile = file;
  const menu = $("ctx");
  menu.innerHTML = "";
  const many = selection.size > 1 && selection.has(file.rel_path);
  const digests = many ? selectedDigests() : [file.sha256];

  if (many) {
    const note = document.createElement("div");
    note.className = "item off";
    note.innerHTML = `<span>${selection.size.toLocaleString()} files selected — ` +
                     `actions apply to all</span>`;
    menu.appendChild(note);
    menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));
  }

  menu.appendChild(menuItem("Find similar images",
    () => findSimilar(file),
    {disabled: file.category !== "image"}));
  menu.appendChild(menuItem("Show details", () => {
    markSelected(file.rel_path); showPane("details"); showDetails(file.rel_path);
  }));
  menu.appendChild(menuItem("View full size", () => viewFullSize(file)));
  menu.appendChild(menuItem("Hex view", () => openHex(file)));
  menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));

  const decided = decisions[file.sha256] || {};
  categories.filter(c => c.code !== uncategorizedCode).forEach(c =>
    menu.appendChild(menuItem(c.name,
      () => categorizeMany(digests, c.code),
      {swatch: true, color: c.color, key: c.key,
       on: decided.category === c.code})));
  // Named as the category it puts the file in, the same word as the key
  // and the category bar use (U12).
  menu.appendChild(menuItem(
    ((categories.find(c => c.code === uncategorizedCode) || {}).name || "Uncategorized") +
    " (clear the category)",
    () => categorizeMany(digests, uncategorizedCode),
    {key: String(uncategorizedCode)}));
  menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));

  caseFlags.forEach(flag =>
    menu.appendChild(menuItem(flag.name,
      () => flagMany(digests, flag.id,
                     !((decided.flags || []).indexOf(flag.id) >= 0)),
      {swatch: true, color: flag.color, key: flag.key,
       on: (decided.flags || []).indexOf(flag.id) >= 0})));
  menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));

  menu.appendChild(menuItem(
    many ? `Export MD5s of ${selection.size.toLocaleString()} files`
         : "Export MD5", () => exportMd5(digests)));
  menu.appendChild(menuItem("Open original file",
    () => desktopOpen(file.rel_path, false)));
  menu.appendChild(menuItem("Show in folder",
    () => desktopOpen(file.rel_path, true)));

  // Keep it on screen: a menu opened near an edge should not run off it.
  menu.style.display = "block";
  const box = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, window.innerWidth - box.width - 8) + "px";
  menu.style.top = Math.max(8, Math.min(y, window.innerHeight - box.height - 8)) + "px";
}

function wireContextMenu(container) {
  container.querySelectorAll("[data-pick]").forEach(el =>
    el.addEventListener("contextmenu", e => {
      const file = lookup(el.dataset.pick);
      if (!file) return;
      e.preventDefault();
      if (!selection.has(file.rel_path)) {
        markSelected(file.rel_path);
        selection = new Set([file.rel_path]);
        lastClicked = file.rel_path;
        renderSelectionState();
        selectedPath = file.rel_path;
      }
      openMenu(file, e.clientX, e.clientY);
    }));
}

async function categorizeMany(digests, code) {
  try {
    const d = await api("/api/case/categorize", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: digests, category: code})});
    $("save-state").textContent = `${d.changed.toLocaleString()} categorized · ` +
      new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    await loadCase();
    setSelection([]);
    afterBulkGrade();
    if (pane === "classify") renderClassification();
    if (selectedPath && pane === "details") showDetails(selectedPath);
  } catch (e) { $("err").textContent = e.message; }
}

async function flagMany(digests, flagId, on) {
  try {
    await api("/api/case/flag", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: digests, flag_id: flagId, on: on})});
    await loadCase();
    render();
    if (selectedPath && pane === "details") showDetails(selectedPath);
  } catch (e) { $("err").textContent = e.message; }
}

async function exportMd5(digests) {
  try {
    const response = await fetch("/api/export/md5", guarded({
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: digests})}));
    if (!response.ok) throw new Error((await response.json()).error || "failed");
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "md5_list.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (e) { $("err").textContent = e.message; }
}

async function findSimilar(file) {
  // Needs the similarity pass to have run: without hashes there is nothing
  // to compare, and saying so beats showing an empty result.
  try {
    const d = await api(`/api/details?job=${jobId}&file=${encodeURIComponent(file.rel_path)}`);
    if (!d.similarity_available) {
      $("err").textContent = "Finding similar images needs Pillow: pip install pillow";
      return;
    }
    if (!d.similarity_ran) {
      $("err").textContent = "Run 'Find similar images' on the Hash Sets tab " +
        "first — the whole run has to be hashed before one file can be compared.";
      return;
    }
    if (!d.similar.length) {
      $("err").textContent = "Nothing in this run looks like that file.";
      return;
    }
    const paths = new Set([file.rel_path, ...d.similar.map(v => v.out_path)]);
    clearAllFilters();
    setSelection([...paths]);
    $("search").value = "";
    // Show exactly those files, by filtering to the selection.
    similarOnly = paths;
    page = 0;
    render();
    $("status").textContent = `${d.similar.length} file(s) look like ` +
      `${file.name} — clear the filter to go back`;
  } catch (e) { $("err").textContent = e.message; }
}

// ---- the full size viewer ---------------------------------------------
// Zoom and pan, because deciding what a picture is often turns on a detail
// that is a few pixels wide on a gallery tile -- a face in a reflection, a
// mark on a wall, the edge of a bed. Scaled to fit is where it starts,
// since that is what somebody wants nine times out of ten.
let zoomScale = 0;          // 0 means "fit the window"
let zoomX = 0, zoomY = 0;   // where the picture sits when it is larger
let zoomDragging = false, zoomFrom = null;

function zoomApply() {
  const picture = $("full-body").querySelector("img, video");
  if (!picture) return;
  // The transform goes on the wrapper, so the masks drawn over the
  // picture move and scale with it.
  const moved = picture.closest(".maskwrap") || picture;
  if (!zoomScale) {
    moved.style.transform = "";
    picture.style.maxWidth = "92vw";
    picture.style.maxHeight = "74vh";
    picture.style.cursor = "zoom-in";
    $("zoom-level").textContent = "fit";
    return;
  }
  picture.style.maxWidth = "none";
  picture.style.maxHeight = "none";
  moved.style.transformOrigin = "0 0";
  moved.style.transform =
    `translate(${zoomX}px, ${zoomY}px) scale(${zoomScale})`;
  picture.style.cursor = zoomDragging ? "grabbing" : "grab";
  $("zoom-level").textContent = `${Math.round(zoomScale * 100)}%`;
}

function zoomTo(scale, atX, atY) {
  const picture = $("full-body").querySelector("img, video");
  if (!picture) return;
  const box = picture.getBoundingClientRect();
  // Keep whatever is under the pointer under the pointer: zooming that
  // moves the thing being looked at is zooming the examiner has to undo.
  const wasScale = zoomScale || (box.width / (picture.naturalWidth ||
                                              picture.videoWidth || box.width));
  const pointX = (atX === undefined ? box.left + box.width / 2 : atX) - box.left;
  const pointY = (atY === undefined ? box.top + box.height / 2 : atY) - box.top;
  const next = Math.min(16, Math.max(0.05, scale));
  if (!zoomScale) { zoomX = box.left - $("full-body").getBoundingClientRect().left;
                    zoomY = box.top - $("full-body").getBoundingClientRect().top; }
  zoomX -= pointX * (next / wasScale - 1);
  zoomY -= pointY * (next / wasScale - 1);
  zoomScale = next;
  zoomApply();
}

function zoomStep(by) {
  const picture = $("full-body").querySelector("img, video");
  if (!picture) return;
  const box = picture.getBoundingClientRect();
  const current = zoomScale ||
    (box.width / (picture.naturalWidth || picture.videoWidth || box.width));
  zoomTo(current * by, box.left + box.width / 2, box.top + box.height / 2);
}

function zoomReset() { zoomScale = 0; zoomX = 0; zoomY = 0; zoomApply(); }

function viewFullSize(file) {
  $("full-title").textContent = file.name;
  const source = `/api/file?job=${jobId}&file=${encodeURIComponent(file.rel_path)}`;
  const media = file.category === "video"
    ? `<video src="${source}" controls autoplay muted></video>`
    : `<img src="${source}" alt="">`;
  $("full-body").innerHTML = `<div class="maskwrap">${media}<div class="masks"></div></div>`;
  $("full-body").classList.toggle("blurred", blurOn());
  fullFile = file;
  setMasking(false);
  zoomReset();
  $("full-modal").classList.add("open");
  paintViewerGrade();
  loadMasks(file.sha256).then(list => drawMasks($("full-body"), list));
}

// What the file in the viewer is graded as, and its flags, in the viewer's
// own header: grading from the viewer moved on to the next picture with
// nothing on screen saying what the last key had done (v2026.11.320, U4).
function gradeText(sha256) {
  const decided = decisions[sha256];
  const code = decided ? decided.category : uncategorizedCode;
  const cat = categories.find(c => c.code === code);
  const flags = ((decided && decided.flags) || []).map(id =>
    (caseFlags.find(f => f.id === id) || {}).name).filter(Boolean);
  return (cat ? cat.name : "Uncategorized") +
         (flags.length ? " · " + flags.join(", ") : "");
}

function paintViewerGrade() {
  if (!fullFile || !$("full-modal").classList.contains("open")) return;
  const decided = decisions[fullFile.sha256];
  const cat = categories.find(c => c.code === (decided ? decided.category : uncategorizedCode));
  const badge = $("full-grade");
  badge.textContent = gradeText(fullFile.sha256);
  badge.style.borderColor = cat && cat.code !== uncategorizedCode ? cat.color : "";
}

let confirmTimer = null;
function confirmGrade(text) {
  const box = $("full-confirm");
  box.textContent = "✓ " + text;
  box.classList.add("show");
  clearTimeout(confirmTimer);
  confirmTimer = setTimeout(() => box.classList.remove("show"), 1600);
}

// One file back or on, in the order the gallery shows. ← and → do this in
// the gallery and in the viewer; the viewer follows the cursor (moveTo).
function stepFile(delta) {
  const list = visible();
  const here = $("full-modal").classList.contains("open") && fullFile
    ? fullFile.rel_path : selectedPath;
  const index = list.findIndex(f => f.rel_path === here);
  if (index < 0) { if (list.length) moveTo(list[0]); return; }
  const target = list[index + delta];
  if (target) { moveTo(target); return; }
  if (delta > 0 && paged) moveTo({after: list[index].rel_path});
}
$("full-prev").addEventListener("click", () => stepFile(-1));
$("full-next").addEventListener("click", () => stepFile(1));
$("full-close").addEventListener("click", () => closeFullSize());
$("zoom-in").addEventListener("click", () => zoomStep(1.4));
$("zoom-out").addEventListener("click", () => zoomStep(1 / 1.4));
$("zoom-fit").addEventListener("click", zoomReset);
$("zoom-actual").addEventListener("click", () => zoomTo(1));

$("full-body").addEventListener("wheel", e => {
  if (!$("full-modal").classList.contains("open")) return;
  const picture = $("full-body").querySelector("img, video");
  if (!picture) return;
  e.preventDefault();
  const box = picture.getBoundingClientRect();
  const current = zoomScale ||
    (box.width / (picture.naturalWidth || picture.videoWidth || box.width));
  zoomTo(current * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
}, {passive: false});

$("full-body").addEventListener("mousedown", e => {
  if (masking || !zoomScale) return;
  zoomDragging = true;
  zoomFrom = {x: e.clientX - zoomX, y: e.clientY - zoomY};
  zoomApply();
  e.preventDefault();
});
document.addEventListener("mousemove", e => {
  if (!zoomDragging || !zoomFrom) return;
  zoomX = e.clientX - zoomFrom.x;
  zoomY = e.clientY - zoomFrom.y;
  zoomApply();
});
document.addEventListener("mouseup", () => {
  if (!zoomDragging) return;
  zoomDragging = false;
  zoomApply();
});
$("full-body").addEventListener("dblclick", () => {
  if (zoomScale) zoomReset(); else zoomTo(1);
});

let hexFile = null, hexOffset = 0;
const HEX_PAGE = 1024;

async function openHex(file, offset) {
  hexFile = file;
  hexOffset = Math.max(0, offset || 0);
  try {
    const d = await api(`/api/hex?job=${jobId}&file=${encodeURIComponent(file.rel_path)}` +
                        `&offset=${hexOffset}&length=${HEX_PAGE}`);
    $("hex-title").textContent = `Hex view — ${file.name}`;
    $("hex-body").textContent = d.lines.join("\n");
    $("hex-where").textContent =
      `bytes ${d.offset.toLocaleString()}–` +
      `${(d.offset + d.length).toLocaleString()} of ${d.size.toLocaleString()}`;
    $("hex-prev").disabled = hexOffset <= 0;
    $("hex-next").disabled = hexOffset + HEX_PAGE >= d.size;
    $("hex-modal").classList.add("open");
  } catch (e) { $("err").textContent = e.message; }
}
$("hex-close").addEventListener("click", () => $("hex-modal").classList.remove("open"));
$("hex-prev").addEventListener("click", () =>
  openHex(hexFile, Math.max(0, hexOffset - HEX_PAGE)));
$("hex-next").addEventListener("click", () => openHex(hexFile, hexOffset + HEX_PAGE));

// ---- the menu, and the report dialog -----------------------------------
// The ribbon replaced the drop-down menu. Everything it offered is on one
// of the seven tabs, and two ways into the same thing is two things to keep
// working.

// ---- the report --------------------------------------------------------
// Everything commits as it happens, so there is nothing to save in the
// usual sense -- but an examiner finishing for the day wants to press
// something and be told where the case is. This writes a snapshot and says
// what is on disk, which is a real thing rather than a reassuring no-op.
async function saveCase() {
  if (!jobId) { $("err").textContent = "Open or run a case first."; return; }
  try {
    const d = await api("/api/case/snapshot", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, label: "manual"})});
    const when = new Date().toLocaleTimeString([], {hour: "2-digit",
                                                   minute: "2-digit"});
    $("save-state").textContent = `case saved · ${when}`;
    $("status").innerHTML =
      `Case saved at ${esc(when)}. Every decision was already written as you ` +
      `made it; this also wrote a snapshot you can go back to:<br>` +
      `<span class="p">${esc(d.path)}</span>`;
  } catch (e) { $("err").textContent = e.message; }
}

$("report-close").addEventListener("click", () =>
  $("report-modal").classList.remove("open"));
$("rp-browse").addEventListener("click", () =>
  openBrowser(p => { $("rp-dest").value = p; }, $("rp-dest").value.trim(),
              "Choose a folder for the report"));

function openFileExport() {
  if (!jobId) { $("err").textContent = "Run or open a case first."; return; }
  $("xf-err").textContent = "";
  $("xf-result").style.display = "none";
  const counts = countsFromDecisions();
  $("xf-categories").innerHTML = categories.map(c => {
    const n = counts[c.code] || 0;
    const checked = (c.code !== uncategorizedCode && n) ? " checked" : "";
    return `<label class="inline" style="min-width:230px;">` +
      `<input type="checkbox" class="xf-cat" value="${c.code}"${checked}> ` +
      `<span class="swatch" style="display:inline-block;width:9px;height:9px;` +
      `border-radius:2px;background:${esc(c.color)};margin-right:5px;"></span>` +
      `${esc(c.name)} <span class="hint">${n.toLocaleString()}</span></label>`;
  }).join("");
  $("export-modal").style.display = "flex";
}

$("display-close").addEventListener("click",
  () => { $("display-modal").style.display = "none"; });

$("logo-close").addEventListener("click",
  () => { $("logo-modal").style.display = "none"; });
$("logo-browse").addEventListener("click", () =>
  openBrowser(p => { $("logo-path").value = p; }, $("logo-path").value.trim(),
              "Choose an image for the report header"));
$("logo-save").addEventListener("click", async () => {
  try {
    const d = await api("/api/logo", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: $("logo-path").value.trim()})});
    $("logo-modal").style.display = "none";
    toast(d.path ? `Reports will carry that logo.` : "Logo removed.");
  } catch (e) { $("logo-err").textContent = e.message; }
});
$("logo-clear").addEventListener("click", async () => {
  try {
    await api("/api/logo", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ""})});
    $("logo-path").value = "";
    $("logo-modal").style.display = "none";
    toast("Logo removed. Reports carry the agency name only.");
  } catch (e) { $("logo-err").textContent = e.message; }
});

$("set-basemap-import").addEventListener("click", importBasemap);
$("settings-close").addEventListener("click",
  () => { $("settings-modal").style.display = "none"; });

$("set-save").addEventListener("click", async () => {
  try {
    await api("/api/settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({examiner: $("set-examiner").value.trim(),
                            agency: $("set-agency").value.trim(),
                            output_root: $("set-output").value.trim(),
                            category_region: $("set-region").value || "US",
                            content_model: $("set-model").value.trim()})});
    $("settings-modal").style.display = "none";
    $("status").textContent = "Settings saved on this machine.";
  } catch (e) { $("set-err").textContent = e.message; }
});

$("set-model-browse").addEventListener("click", () =>
  openBrowser(p => { $("set-model").value = p; }, $("set-model").value.trim(),
              "Choose an ONNX image model"));

$("set-forget").addEventListener("click", async () => {
  if (!confirm("Forget everything this machine remembers?\n\n" +
               "The examiner and agency, the output folder, and the hash " +
               "store address, certificate and token. Cases and the store " +
               "itself are untouched.")) return;
  try {
    await api("/api/store/forget", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({all: true})});
    $("set-examiner").value = "";
    $("set-agency").value = "";
    $("set-output").value = "";
    $("set-token").textContent = "No hash store token is stored on this machine.";
    $("status").textContent = "This machine has forgotten its settings.";
  } catch (e) { $("set-err").textContent = e.message; }
});

$("export-close").addEventListener("click",
  () => { $("export-modal").style.display = "none"; });

$("xf-browse").addEventListener("click", () =>
  openBrowser(p => { $("xf-dest").value = p; }, $("xf-dest").value.trim(),
              "Choose a folder to export into"));

$("xf-go").addEventListener("click", async () => {
  const dest = $("xf-dest").value.trim();
  const chosen = [...document.querySelectorAll(".xf-cat:checked")]
    .map(i => parseInt(i.value, 10));
  if (!dest) { $("xf-err").textContent = "Say where the files should go."; return; }
  if (!chosen.length) {
    $("xf-err").textContent = "Choose at least one category."; return;
  }
  $("xf-err").textContent = "";
  $("xf-go").disabled = true;
  $("xf-go").textContent = "Exporting…";
  try {
    const d = await api("/api/export/files", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, dest: dest, categories: chosen,
                            include_uncategorized:
                              chosen.indexOf(uncategorizedCode) >= 0,
                            by_category: $("xf-bycat").checked,
                            by_kind: $("xf-bykind").checked,
                            thumbnails: $("xf-thumbs").checked})});
    const trouble = (d.failed || []).length + (d.mismatched || []).length;
    // A copy with no recorded hash was checked against nothing; "every copy
    // matched" is only true when there are none of those either.
    const unchecked = (d.unverified || []).length;
    $("xf-result").innerHTML =
      `Wrote <strong>${d.written.toLocaleString()}</strong> file(s) into ` +
      `${(d.categories || []).length} folder(s).<br>` +
      `<span class="p">${esc(d.folder)}</span><br>` +
      (trouble
        ? `<span class="warn">${(d.failed || []).length} could not be copied; ` +
          `${(d.mismatched || []).length} did not match the hash recorded ` +
          `when they were carved. Both are listed in exported_files.csv.</span>`
        : unchecked
          ? `<span class="warn">${unchecked.toLocaleString()} had no hash ` +
            `recorded when they were carved, so their copies could not be ` +
            `checked; hash_verified is blank for them in exported_files.csv. ` +
            `The rest matched.</span>`
          : `<span class="hint">Every copy matched the hash recorded when it ` +
            `was carved. The manifest is exported_files.csv beside them.</span>`);
    $("xf-result").style.display = "block";
  } catch (e) { $("xf-err").textContent = e.message; }
  finally { $("xf-go").disabled = false; $("xf-go").textContent = "Export"; }
});

async function openReport() {
  if (!jobId) { $("err").textContent = "Run or open a case first."; return; }
  $("rp-err").textContent = "";
  // The list is built when the dialog opens as well as on change: opening
  // it with "the categories I choose" already selected should not show an
  // empty box.
  const pickingNow = $("rp-scope").value === "categories";
  $("rp-categories").style.display = pickingNow ? "block" : "none";
  if (pickingNow) renderReportCategories();
  // Filled from wherever it is already known -- the form that started the
  // run, the case as it was registered, then this machine's Settings -- so
  // a reopened case's report does not go out with an empty header
  // (v2026.11.320, U10).
  let kept = {};
  try { kept = await api("/api/settings"); } catch (e) { /* none saved */ }
  const known = openedCase || {};
  const fill = (id, ...values) => {
    if (!$(id).value.trim())
      $(id).value = values.map(v => (v || "").trim()).find(Boolean) || "";
  };
  fill("rp-case", $("case-number").value, known.case_number, kept.case_number);
  fill("rp-examiner", $("case-examiner").value, known.examiner, caseExaminer, kept.examiner);
  fill("rp-evidence", $("case-evidence").value, known.evidence);
  fill("rp-agency", kept.agency);
  const missing = [["rp-case", "case number"], ["rp-examiner", "examiner"]]
    .filter(([id]) => !$(id).value.trim()).map(([, name]) => name);
  $("rp-warn").textContent = missing.length
    ? `No ${missing.join(" or ")} — the report's front page will show it blank.` : "";
  const outDir = (lastSummary && lastSummary.output_dir) || known.out_dir || "";
  $("rp-dest").placeholder = outDir
    ? `${outDir}${outDir.includes("\\") ? "\\" : "/"}report (the default)`
    : "a folder for the report";
  if (!$("rp-fields").dataset.built) {
    try {
      const d = await api("/api/report/fields");
      $("rp-fields").innerHTML = d.fields.map(f =>
        `<label class="inline"><input type="checkbox" value="${esc(f.key)}"` +
        `${d.default.indexOf(f.key) >= 0 ? " checked" : ""}> ${esc(f.label)}</label>`
      ).join("");
      $("rp-fields").dataset.built = "1";
    } catch (e) { /* the defaults will be used */ }
  }
  updateScopeNote();
  $("report-modal").classList.add("open");
}

function updateScopeNote() {
  const mode = $("rp-scope").value;
  const shown = paged ? pv.total : visible().length;
  const note = {
    all: `every file in this run (${caseSize().toLocaleString()})`,
    filter: `what the filters show now (${shown.toLocaleString()})`,
    selection: `${selection.size.toLocaleString()} selected file(s)`,
    graded: "only files that have been categorized",
  }[mode];
  $("rp-scope-note").textContent = `The report will cover ${note}, and will ` +
    `say so on its front page.`;
}
function renderReportCategories() {
  // Every category the case uses, with its count, so nobody has to guess
  // what they are leaving out. Ticked for the ones in use, because a report
  // of nothing is never what was meant.
  const box = $("rp-category-list");
  if (!box) return;
  const counts = countsFromDecisions();
  box.innerHTML = categories.map(c => {
    const n = counts[c.code] || 0;
    const checked = (c.code !== uncategorizedCode && n) ? " checked" : "";
    return `<label class="inline" style="min-width:240px;">` +
      `<input type="checkbox" class="rp-cat" value="${c.code}"${checked}> ` +
      `<span class="swatch" style="display:inline-block;width:9px;height:9px;` +
      `border-radius:2px;background:${esc(c.color)};margin-right:5px;"></span>` +
      `${esc(c.name)} <span class="hint">${n.toLocaleString()}</span></label>`;
  }).join("");
}

$("rp-scope").addEventListener("change", () => {
  updateScopeNote();
  const picking = $("rp-scope").value === "categories";
  $("rp-categories").style.display = picking ? "block" : "none";
  if (picking) renderReportCategories();
});

$("rp-go").addEventListener("click", async () => {
  $("rp-err").textContent = "";
  const formats = [...$("rp-formats").querySelectorAll("input:checked")]
    .map(i => i.value);
  if (!formats.length) { $("rp-err").textContent = "Choose at least one format."; return; }
  const fields = [...$("rp-fields").querySelectorAll("input:checked")].map(i => i.value);

  const mode = $("rp-scope").value;
  const body = {
    job: jobId, dest: $("rp-dest").value.trim(),
    title: $("rp-title").value.trim(), agency: $("rp-agency").value.trim(),
    case_number: $("rp-case").value.trim(),
    evidence: $("rp-evidence").value.trim(),
    examiner: $("rp-examiner").value.trim(), notes: $("rp-notes").value,
    formats: formats, fields: fields,
    thumbnails: $("rp-thumbs").checked, blur: $("rp-blur").checked,
    full_copies: $("rp-full").checked, group_by_flag: $("rp-byflag").checked,
    timezone: timeZone,
    include_uncategorized: mode !== "graded",
  };
  if (mode === "categories") {
    const chosen = [...$("rp-category-list").querySelectorAll(".rp-cat:checked")]
      .map(i => parseInt(i.value, 10));
    if (!chosen.length) {
      $("rp-err").textContent = "Choose at least one category to include.";
      return;
    }
    body.categories = chosen;
    body.include_uncategorized = chosen.includes(uncategorizedCode);
  } else if (mode === "selection") {
    if (!selection.size) { $("rp-err").textContent = "Nothing is selected."; return; }
    body.sha256 = paged ? await digestsFor([...selection]) : selectedDigests();
  } else if (mode === "filter") {
    if (paged) {
      try {
        const d = await galleryQuery({ids: true, limit: 1});
        body.sha256 = [...new Set((d.ids || []).map(([, digest]) => digest))];
      } catch (e) { $("rp-err").textContent = e.message; return; }
    } else {
      body.sha256 = [...new Set(visible().map(f => f.sha256))];
    }
  }

  $("rp-go").disabled = true;
  $("rp-go").textContent = "Writing…";
  $("rp-result").style.display = "block";
  let reportId = null;
  try {
    const started = await api("/api/report/build", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)});
    reportId = started.id;
    // A report of a large case takes minutes, nearly all of it thumbnails.
    // Watching it is the difference between a slow job and a hung one.
    while (true) {
      await new Promise(r => setTimeout(r, 700));
      const d = await api(`/api/report/status?id=${reportId}`);
      if (d.status === "running") {
        const share = d.total ? Math.min(99, Math.round(d.done / d.total * 100)) : 0;
        $("rp-result").innerHTML =
          `<strong>${esc(d.phase)}</strong> — ${d.done.toLocaleString()}` +
          (d.total ? ` of ${d.total.toLocaleString()} (${share}%)` : "") +
          ` <button class="secondary" id="rp-cancel" style="padding:1px 9px;">Stop</button>`;
        const stop = $("rp-cancel");
        if (stop) stop.addEventListener("click", () => {
          api("/api/report/cancel", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({id: reportId})}).catch(() => {});
        });
        continue;
      }
      if (d.status === "canceled") {
        $("rp-result").innerHTML = "Stopped. Nothing further was written.";
        break;
      }
      if (d.status === "error") {
        $("rp-err").textContent = d.error;
        $("rp-result").style.display = "none";
        break;
      }
      const pages = (d.written || []).reduce(
        (n, w) => n + ((w.pages && w.pages.length) || 1), 0);
      $("rp-result").innerHTML =
        `Covered <strong>${d.files.toLocaleString()}</strong> of ` +
        `${d.of.toLocaleString()} file(s)` +
        (pages > (d.written || []).length
          ? `, across ${pages} page(s)` : "") + `.<br>` +
        (d.written || []).map(w => w.skipped
          ? `<span class="warn">skipped: ${esc(w.skipped)}</span>`
          : `<span class="p">${esc(w.path)}</span>`).join("<br>");
      break;
    }
  } catch (e) { $("rp-err").textContent = e.message; }
  finally { $("rp-go").disabled = false; $("rp-go").textContent = "Write report"; }
});

// ---- the case list -----------------------------------------------------
async function rebuildCase(outDir) {
  if (!confirm(`This run did not finish. Its files and the list it had ` +
               `written are still there, so the list can be rebuilt from ` +
               `them.\n\nThe case will be partial: whatever the run had ` +
               `not reached is not in it.\n\nRebuild?`)) return;
  try {
    const d = await api("/api/case/rebuild", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({out_dir: outDir})});
    await loadCases();
    toast(`${count(d.files)} file(s) recovered from the interrupted run.` +
          `<br><span class="hint">${esc(d.note)}</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function loadCases() {
  try {
    const d = await api("/api/cases");
    $("cases-registry").textContent = `List kept in ${d.path}`;
    $("cases-list").innerHTML = d.cases.length
      ? `<div class="caselist">` + d.cases.map(c => {
          const title = c.case_number || c.evidence ||
                        c.out_dir.split(/[\\/]/).pop();
          return `<div class="case ${c.present ? "" : "missing"}" ` +
            `data-open="${esc(c.out_dir)}">
            <div class="who">
              <div class="num">${esc(title)}` +
              (c.evidence && c.evidence !== title
                ? ` <span class="hint">${esc(c.evidence)}</span>` : "") +
              // Three states, not two. A run that was interrupted leaves
              // its folder and its files behind and only the summary
              // missing, which used to read as "folder missing" and gave
              // an examiner no reason to look inside.
              (c.present ? ""
                : c.unfinished
                  ? ` <span class="tag del">unfinished — ` +
                    `<a href="#" data-rebuild="${esc(c.out_dir)}">rebuild ` +
                    `its list</a></span>`
                  : c.folder_there
                    ? ` <span class="tag del">nothing recoverable here</span>`
                    : ` <span class="tag del">folder missing</span>`) +
              `</div>
              <div class="sub">${esc(c.source || c.out_dir)}</div>
              <div class="sub">${esc(c.out_dir)}</div>
            </div>
            <div class="stat"><strong>${c.files.toLocaleString()}</strong>files</div>
            <div class="stat"><strong>${c.images.toLocaleString()}</strong>images</div>
            <div class="stat"><strong>${c.videos.toLocaleString()}</strong>videos</div>
            <div class="stat"><strong>${c.categorized.toLocaleString()}</strong>graded</div>
            <div class="stat">${esc((c.last_opened || c.created_at).slice(0, 10))}
              <br>${esc(c.examiner || "")}</div>
            <button class="secondary tiny" data-forget="${esc(c.out_dir)}"
                    title="Remove from this list. The output folder is left alone.">✕</button>
          </div>`;
        }).join("") + `</div>`
      : `<p class="hint">No cases yet. Run an extraction, or scan a folder ` +
        `holding output from earlier runs.</p>`;

    $("cases-list").querySelectorAll("[data-rebuild]").forEach(link =>
    link.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      rebuildCase(link.dataset.rebuild);
    }));
  $("cases-list").querySelectorAll("[data-open]").forEach(el =>
      el.addEventListener("click", e => {
        if (e.target.dataset.forget !== undefined) return;
        if (el.classList.contains("missing")) return;
        openCase(el.dataset.open);
      }));
    $("cases-list").querySelectorAll("[data-forget]").forEach(b =>
      b.addEventListener("click", async e => {
        e.stopPropagation();
        const gone = await api("/api/cases/forget", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({out_dir: b.dataset.forget})});
        await loadCases();
        // Taken off the list at once, and put back with one click: a stray
        // ✕ used to lose a case's place in the list for good (U12).
        if (gone.forgotten) {
          lastForgotten = gone.forgotten;
          toast(`Removed ${esc(gone.forgotten.case_number || gone.forgotten.out_dir)} ` +
                `from the list; its folder is untouched. ` +
                `<button class="secondary tiny" id="forget-undo">Undo</button>`);
          const undo = $("forget-undo");
          if (undo) undo.addEventListener("click", restoreForgotten);
        }
      }));
  } catch (e) { $("cases-err").textContent = e.message; }
}

let lastForgotten = null;
async function restoreForgotten() {
  if (!lastForgotten) return;
  try {
    await api("/api/cases/restore", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(lastForgotten)});
    toast(`${esc(lastForgotten.case_number || lastForgotten.out_dir)} is back in the list.`);
    lastForgotten = null;
    loadCases();
  } catch (e) { toast(esc(e.message), true); }
}

async function openCase(outDir) {
  $("cases-err").textContent = "";
  const ticket = claimPage();
  try {
    const d = await api("/api/cases/open", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({out_dir: outDir})});
    if (ticket !== pageTicket) return;      // a newer open or run took the page
    const st = await api(`/api/status?job_id=${d.job_id}`);
    if (ticket !== pageTicket) return;
    jobId = d.job_id;
    openedCase = Object.assign({out_dir: outDir}, d.case || {});
    $("job-badge").style.display = "inline-block";
    $("job-badge").textContent = caseBadge();
    files = takeFiles(st, []);
    clearAllFilters();
    showSummary(st.summary, "done");
    showFinished("done", st.summary, caseSize());
    $("export-box").style.display = "block";
    $("vic-box").style.display = "block";
    enterReview((st.summary && st.summary.image) || outDir);
    await loadCase();
    render();
    // Straight to the case itself, which is what opening one is for.
    showTab("review");
    toast(`Opened ${esc(d.case.case_number || outDir)} — ` +
          `${caseSize().toLocaleString()} file(s), nothing re-carved.`);
  } catch (e) { $("cases-err").textContent = e.message; }
}

let workspace = "cases";

function showTab(name) {
  // The workspace panels. The ribbon no longer shows them as tabs, so
  // whichever is open is tracked here and the ribbon lights the item that
  // leads to it.
  workspace = name;
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach(c =>
    c.classList.toggle("active", c.id === "tab-" + name));
  renderRibbon();
}

// ---- the ribbon --------------------------------------------------------
// Seven tabs, in the order the work happens: the program itself, the case in
// hand, getting data in, getting findings out, how the gallery looks, and
// the tools. Every item here calls something that already exists -- a
// ribbon that offers what the program cannot do is worse than no ribbon.
let ribbonTab = "home";

function ribbonDefinition() {
  const haveCase = !!jobId;
  const havePicked = !!selectedPath;
  const picked = () => lookup(selectedPath);

  return {
    file: [
      {name: "Case", items: [
        {label: "New extraction", glyph: "✛", tip: "Starts a new run: choose the image, folder or phone extraction to read, then press Load / Inspect.", go: newExtraction,
         on: () => workspace === "review"},
        {label: "Open case", glyph: "▤", tip: "Opens a case already processed. Nothing is carved or hashed again — its manifest and your gradings are read back.", go: () => showTab("cases"),
         on: () => workspace === "cases"},
        {label: "Back to the case", glyph: "▣", tip: "Returns to the gallery you were working in.", go: () => showTab("review"),
         needs: haveCase, on: () => workspace === "review"},
        {label: "Save case", glyph: "💾", tip: "Writes a snapshot of the case now. Every grading is already saved as you make it; this is a marker you can return to.", go: saveCase, needs: haveCase},
        {label: "Close case", glyph: "⏏", tip: "Saves the case and returns to the list. Your decisions are on disk either way.", go: closeCase, needs: haveCase},
        // The drive back, without closing the program.
        {label: "Release image", glyph: "⎋", tip: "Lets go of the evidence so the drive can be detached, without closing the program. A finished run lets go by itself.", go: releaseImage},
      ]},
      // What outlives a case. These are set once for the machine or the
      // unit, not per case, and keeping them apart from the case's own
      // controls is the difference between a setting and a decision.
      {name: "Set up once", items: [
        {label: "Hash store", glyph: "⛁", tip: "The store this unit shares: Project VIC, NSRL and every case anyone has contributed. Connect to it, check it, or add to it here.", go: () => showTab("hashes")},
        {label: "My stash", glyph: "⊙", tip: "Your own gradings from earlier cases, kept on this machine. A match is what you decided before, not what the unit has agreed.", go: showStash},
        {label: "Import C4All index", glyph: "⇤", tip: "Takes the gradings out of a C4All index (C4P or C4M Index.xml) and applies them here by hash. Files you have already graded keep your grading.", go: importC4All,
         needs: haveCase},
        {label: "Hash sets", glyph: "☰", tip: "Import a set — Project VIC, NSRL, or your own list — into the store once, so every case from now on is checked against it.", go: () => {
          showTab("hashes");
          toast("Import a set into the store to have every case from now " +
                "on checked against it.");
        }},
        {label: "Custom Signatures", glyph: "▦", tip: "Teach the carver a format it does not know: point it at sample files and it works out the bytes that begin them. Applies to runs started from this program.", go: () => showTab("sigs")},
        {label: "Agency logo", glyph: "◳", tip: "The picture put on the header of every report this machine writes, unless a case gives its own.", go: openLogo},
      ]},
      {name: "This machine", items: [
        {label: "Settings", glyph: "⚙", tip: "Examiner name, agency, where output goes, the time zone reports use, and the paths to any models.", go: openSettings},
        {label: "What is installed", glyph: "✇", tip: "What this machine can actually do: which libraries are present and which evidence formats they make readable.", go: showInstalled},
      ]},
      {name: "Help", items: [
        {label: "Manual", glyph: "?", tip: "The full manual for this program.",
         go: () => window.open("/manual", "_blank", "noopener")},
        {label: "What it does not do", glyph: "⚖",
         tip: "What this program does, how that has been tested, and at " +
              "equal length what it does not do and what has not been " +
              "tested. Written to be handed over as it stands.",
         go: () => window.open("/doc/validation", "_blank", "noopener")},
        {label: "Checks to run here", glyph: "☑",
         tip: "The checks that can only be run on this machine, because " +
              "they need its hardware or its libraries -- and what each " +
              "one proves.",
         go: () => window.open("/doc/tasks", "_blank", "noopener")},
        {label: "Installing", glyph: "⤓",
         tip: "Installing on another machine, keeping a unit's copies in " +
              "step, and who should hold which account on the shared " +
              "store.",
         go: () => window.open("/doc/deployment", "_blank", "noopener")},
        {label: "What changed", glyph: "≡",
         tip: "Every change to this program and why, newest first. The " +
              "answer to \"which version produced this report, and what " +
              "was different about it\".",
         go: () => window.open("/doc/changes", "_blank", "noopener")},
        {label: "Licenses", glyph: "©",
         tip: "This program's MIT license and its no-warranty terms, and the " +
              "license of everything it includes: The Sleuth Kit (Copyright " +
              "(c) 1997,1998,1999, International Business Machines Corporation " +
              "and others), libewf, the models and the rest.",
         go: () => window.open("/doc/licenses", "_blank", "noopener")},
        {label: "Hash store setup", glyph: "⌸", tip: "How to set up the shared hash store and its server, step by step.",
         go: () => window.open("/server-setup", "_blank", "noopener")},
      ]},
    ],
    home: [
      {name: "Case", items: [
        // First, and on every tab that leads away from it: going to the
        // hash store should not mean opening the case again to get back.
        {label: "Back to the case", glyph: "▣", tip: "Returns to the gallery you were working in.", go: () => showTab("review"),
         needs: haveCase, on: () => workspace === "review"},
        {label: "Save case", glyph: "💾", tip: "Writes a snapshot of the case now. Every grading is already saved as you make it; this is a marker you can return to.", go: saveCase, needs: haveCase},
        {label: "Refresh", glyph: "↻", tip: "Reads the case from disk again, picking up anything another window or another examiner has changed.", go: async () => {
          await loadCase(); render(); toast("Case reloaded from disk.");
        }, needs: haveCase},
        {label: "Undo", glyph: "↶", tip: "Reverses the last grading. The audit log keeps every change, so a case can be rebuilt from it if more than one needs reversing.", go: undoLast, needs: haveCase},
      ]},
      {name: "Hash store", items: [
        {label: "Manage store", glyph: "⛁", tip: "What the store holds, and where it is: the sets imported into it and how many cases have contributed.", go: () => showTab("hashes")},
        {label: "Check this case", glyph: "⚲", tip: "Looks every file in this case up in the store and your stash. A match carries what that set said, and says which set said it.", go: () => {
          showTab("hashes"); $("store-scan").click();
        }, needs: haveCase},
        {label: "Add this case", glyph: "⤴", tip: "Sends this case's gradings to the shared store, as hashes and categories only — never a picture, never a path.", go: () => {
          showTab("hashes"); $("store-contribute").click();
        }, needs: haveCase},
      ]},
      {name: "While reviewing", items: [
        {label: "Blur", glyph: "◍", go: () => $("blur-on").click(), tip: "The review blur: blurs the categories you chose, and lifts when you hover a tile. For hiding the screen from the room, use Ctrl-B, which blurs everything and lifts for nothing.",
         on: () => blurOn()},
        {label: "Hide known software", glyph: "⊘",
        tip: "Hides files an imported set says are known software — NSRL and the like — so the gallery holds what somebody put on the device rather than what Windows did.",
         tip: "Hides files an imported set says are known software — NSRL " +
              "and the like — so the gallery holds what somebody put there " +
              "rather than what Windows did.",
         go: () => $("chip-nsrl").click(), on: () => hideKnownSoftware},
        {label: "Stack similar", glyph: "⧉",
         tip: "One tile per group of near-identical pictures instead of one per file. Grading the stack grades every file in it.",
         go: () => $("chip-stack").click(), on: () => stackSimilar},
      ]},
    ],
    "case data": [
      {name: "Bring in", items: [
        {label: "Back to the case", glyph: "▣", tip: "Returns to the gallery you were working in.", go: () => showTab("review"),
         needs: haveCase, on: () => workspace === "review"},
        {label: "New extraction", glyph: "✛", tip: "Starts a new run: choose the image, folder or phone extraction to read, then press Load / Inspect.", go: newExtraction},
        {label: "Open a case", glyph: "▤", tip: "Opens a case already processed. Nothing is carved or hashed again — its manifest and your gradings are read back.", go: () => showTab("cases")},
        {label: "Find moved cases", glyph: "⌖", tip: "Searches a folder for cases whose output has been moved, and points the list at where they are now.", go: () => {
          showTab("cases"); $("cases-scan").scrollIntoView({block: "center"});
        }},
      ]},
      {name: "Check against", items: [
        // Opens the store. It used to run a check against the case as
        // well, which is why a button labelled "Hash store" complained
        // that the job had no manifest -- checking a case is its own
        // button, on Home.
        {label: "Hash store", glyph: "⛁",
         tip: "Opens the shared store: what it holds, what is connected, " +
              "and how to add a set. Checking this case against it is " +
              "\"Check this case\" on the Home tab.",
         go: () => showTab("hashes")},
        {label: "Loaded sets", glyph: "☰",
         tip: "Checks this case against a set loaded for this session " +
              "only, rather than against the store.",
         go: () => { showTab("hashes"); $("hash-scan").click(); },
         needs: haveCase},
      ]},
      {name: "Work out", items: [
        {label: "Similar images", glyph: "≈", tip: "Groups pictures that look alike across the whole case — the same photograph re-saved, resized or lightly edited. Finds copies of a picture, not other pictures of the same subject.", go: () => {
          showTab("similar"); $("sim-scan").click();
        }, needs: haveCase},
        {label: "Stills to videos", glyph: "▸", tip: "Matches still pictures against the frames of the videos in this case, so a screenshot or a saved frame is tied back to the clip it came from.", go: () => {
          showTab("similar"); $("frames-match").click();
        }, needs: haveCase},
        {label: "Screening", glyph: "◑", tip: "Measures skin tone and detects faces across the case, so what is most likely to need looking at sorts to the front. It measures; it never categorizes.", go: () => {
          showTab("similar"); $("screen-scan").click();
        }, needs: haveCase},
        {label: "Content Credentials", glyph: "⛨", tip: "Reads what a file states about itself — what made it, whether it says it was AI-generated — and checks the signature where a trust list is set. Nothing here is proof of who made a file.", go: readCredentials,
         needs: haveCase},
      ]},
    ],
    "report / export": [
      {name: "Report", items: [
        {label: "Create report", glyph: "🖹", tip: "Builds the report: HTML to read, Word for a hand-over, or the protocol document with its conclusion tables.", go: openReport, needs: haveCase},
      ]},
      {name: "Files", items: [
        {label: "Export files", glyph: "⇪", tip: "Copies the graded files out, hashing each copy as it is written and checking it against the hash taken at carve time.", go: openFileExport, needs: haveCase},
        {label: "MD5 list", glyph: "#", tip: "A plain list of the MD5s of the files you have graded, for a tool that wants nothing else.", go: exportMd5, needs: haveCase},
      ]},
      {name: "Other tools", items: [
        {label: "Project VIC", glyph: "◈", tip: "Writes ProjectVIC.json for Griffeye. Load that file, not manifest.json, which is this program's own record.", go: () => exportAs("projectvic"),
         needs: haveCase},
        {label: "LAVA", glyph: "◐", tip: "Writes a LAVA set and can open it.", go: () => exportAs("lava"), needs: haveCase},
        {label: "C4All", glyph: "◇", tip: "Writes a C4All index beside the exported files.", go: () => exportAs("c4all"), needs: haveCase},
        {label: "KML", glyph: "⌘", tip: "Writes the locations of the pictures and videos that carry them, for a map.", go: () => exportAs("kml"), needs: haveCase},
      ]},
    ],
    view: [
      {name: "Panes", items: [
        {label: "Details", glyph: "▥", tip: "The pane showing everything known about the selected file: its hashes, dates, camera, location and where it was found.", go: () => $("btn-details").click(),
         on: () => detailsOpen && pane === "details"},
        {label: "Classification", glyph: "▦", tip: "The pane for grading: the categories, their keys and what is already decided.",
         go: () => $("btn-classify").click(),
         on: () => detailsOpen && pane === "classify"},
        {label: "Filters", glyph: "▽", tip: "The pane for narrowing the gallery — by category, flag, kind, or what a hash set said.", go: () => $("btn-filters").click(),
         on: () => detailsOpen && pane === "filters"},
      ]},
      {name: "How it looks", items: [
        {label: "Gallery", glyph: "▦",
         tip: "Shows the case as tiles, which is how grading is done — " +
              "the keyboard keys act on the tile you are on.",
         go: () => document.querySelector('[data-view="gallery"]').click(),
         on: () => view === "gallery"},
        {label: "Table", glyph: "☰",
         tip: "Shows the case as rows, with the columns you can sort on.",
         go: () => document.querySelector('[data-view="table"]').click(),
         on: () => view === "table"},
      ]},
      {name: "Gallery", items: [
        {label: "Stack similar", glyph: "⧉", go: () => $("chip-stack").click(), tip: "Shows one tile per group of near-identical pictures instead of one per file. Grading the stack grades every file in it.",
         on: () => stackSimilar},
        {label: "Show containers", glyph: "🗀", tip: "Shows the archives, documents and databases that media came out of. They are hidden by default because a zip whose pictures are already listed is a tile showing nothing.",
         go: () => $("chip-containers").click(), on: () => showContainers},
        {label: "Hide known software", glyph: "⊘",
         tip: "Hides files an imported set says are known software — NSRL and the like — so the gallery holds what somebody put on the device rather than what Windows did.",
         go: () => $("chip-nsrl").click(), on: () => hideKnownSoftware},
        {label: "Grayscale", glyph: "◐", go: () => $("gray-on").click(),
         tip: "Shows every picture and video in grey. How it is shown only; " +
              "the files are untouched. Cover part of one picture with Mask " +
              "in the full-size view.",
         on: () => grayOn()},
        {label: "Blur", glyph: "◍", go: () => $("blur-on").click(),
         tip: "The review blur: blurs the categories you chose, and lifts " +
              "when you hover a tile. To hide the screen from the room use " +
              "Ctrl-B, which blurs everything and lifts for nothing.",
         on: () => blurOn()},
      ]},
      {name: "Map", items: [
        {label: "Case map", glyph: "⌖",
         tip: "Every file the gallery's filter shows that carries a location, on " +
              "the offline basemap. Click a point to go to the file.",
         go: openCaseMap, needs: haveCase},
      ]},
      {name: "Reset", items: [
        {label: "Clear filters", glyph: "✕", tip: "Shows the whole case again.", go: clearAllFilters},
      ]},
    ],
    display: [
      {name: "Tiles", items: [
        {label: "Tile size", control: "tile-size",
         tip: "How large each tile is drawn. Smaller fits more of the " +
              "case on a page; larger is what a picture that is hard to " +
              "judge usually needs."},
      ]},
      {name: "Page", items: [
        {label: "Per page", control: "per-page",
         tip: "How many files a page holds. A larger page means fewer " +
              "pages to walk through and longer to draw each one."},
        {label: "Hold the page", control: "hold-page",
         tip: "Keeps the page as it was built while you grade it, so " +
              "tiles do not shift under the cursor. Turn it off to " +
              "follow a run as it recovers more."},
      ]},
      {name: "Times", items: [
        {label: "Time zone", control: "tz-select",
         tip: "Which zone times are read in. Stored times never change; " +
              "a report states the zone it used."},
      ]},
      {name: "Blur", items: [
        {label: "Review blur", control: "blur-on",
         tip: "Blurs the categories chosen beside it, and lifts when you " +
              "hover a tile."},
        {label: "What is blurred", control: "blur-scope",
         tip: "Whether the blur covers the categories chosen, every " +
              "picture, or only what is not yet graded."},
        {label: "Blurred categories", glyph: "☷", go: openDisplayOptions,
         tip: "Choose which of this case's categories the blur covers when it " +
              "is set to the categories ticked."},
        {label: "Hide the screen", glyph: "⬛",
         tip: "Blurs every picture and video on the page and lifts for " +
              "nothing, for when somebody walks up to the desk. Ctrl-B " +
              "does the same from anywhere.",
         go: () => hideScreen(!document.body.classList
                   .contains("hidden-screen")),
         on: () => document.body.classList.contains("hidden-screen")},
      ]},
    ],
    apps: [
      {name: "This file", items: [
        // These take the file, not its path. Handing them the path is why
        // the ribbon did nothing while right-clicking the same picture
        // worked: the context menu passes the file itself.
        {label: "Hex view", glyph: "⌗", tip: "Shows the raw bytes of the selected file, with its signature, for when what a file claims to be and what it is may differ.", go: () => {
          const f = picked(); if (f) openHex(f, 0);
        }, needs: havePicked},
        {label: "Full size", glyph: "⤢", tip: "Opens the selected picture full size, with scroll to zoom, drag to pan, and + - 0 1 on the keyboard.", go: () => {
          const f = picked(); if (f) viewFullSize(f);
        }, needs: havePicked},
        {label: "Find similar", glyph: "≈", tip: "Pictures that look like the selected one. The same search as Similar images on the Case Data tab, started from one file rather than run over the whole case.", go: () => {
          const f = picked(); if (f) findSimilar(f);
        }, needs: havePicked},
        // The one that survives an edit: cropped, rotated, mirrored,
        // captioned, pasted into a screenshot.
        {label: "Find faces", glyph: "☻", tip: "Finds the faces in the case so one of them can be searched for. Needs the two face models; a score is never an identification.", go: findFaces, needs: haveCase},
        // What is in the picture, rather than which file it is.
        {label: "Others like this", glyph: "◎", tip: "Orders the case by what is in the picture rather than which file it is — other pictures of the same room or subject. Needs an image model and OpenCV 5.", go: () => {
          const f = picked(); if (f) findLikeThis(f);
        }, needs: havePicked},
        {label: "Find edited copies", glyph: "⟲", tip: "The same picture after somebody cropped, rotated, mirrored, captioned or pasted it into a screenshot. Finds the same picture, never the same subject.", go: () => {
          const f = picked(); if (f) findEditedCopies(f);
        }, needs: havePicked},
      ]},
      {name: "On this machine", items: [
        {label: "Open with…", glyph: "⧉", tip: "Opens the selected file in whatever this machine normally uses for it.",
         go: () => desktopOpen(selectedPath, false), needs: havePicked},
        {label: "Show in folder", glyph: "🗀", tip: "Opens the folder holding the selected file, with it highlighted.",
         go: () => desktopOpen(selectedPath, true), needs: havePicked},
        {label: "Output folder", glyph: "▤", tip: "Opens the case's own folder: the recovered files, the manifest, the reports and the exports.",
         go: () => desktopOpen("", true), needs: haveCase},
      ]},
      {name: "Case history", items: [
        {label: "Audit log", glyph: "☷", tip: "Every change made to this case, with who made it and when. A case can be rebuilt from it after a mistake.", go: showAuditLog, needs: haveCase},
      ]},
    ],
  };
}

async function showAuditLog() {
  // Everything done to this case, in the order it happened. The same view
  // the menu used to give, reached from the ribbon now.
  if (!jobId) return;
  try {
    const d = await api(`/api/case/audit?job=${jobId}`);
    $("hex-title").textContent = "Audit log";
    $("hex-body").textContent = d.audit.map(r =>
      `${r.at}  ${(r.who || "-").padEnd(14)}  ${r.action.padEnd(20)}  ${r.detail}`
    ).join("\n") || "Nothing recorded yet.";
    $("hex-where").textContent =
      `${d.audit.length} entr(y/ies), most recent first`;
    $("hex-prev").disabled = true;
    $("hex-next").disabled = true;
    $("hex-modal").classList.add("open");
  } catch (e) { $("err").textContent = e.message; }
}

async function showInstalled() {
  try {
    const c = await api("/api/capabilities");
    const extra = c.vendor_fs || {};
    $("hex-title").textContent = "What is installed";
    $("hex-body").textContent = [
      `engine         : ${c.engine || ""}`,
      `file systems   : ${(c.filesystems || []).join(", ")}`,
      `also readable  : ${extra.available ? (extra.kinds || []).join(", ")
                                          : "the vendored reader is absent"}`,
      `similarity     : ${c.similarity && c.similarity.available
                          ? "yes" : (c.similarity || {}).message || "no"}`,
      `video frames   : ${c.video_frames && c.video_frames.available
                          ? "OpenCV " + c.video_frames.version
                          : (c.video_frames || {}).message || "no"}`,
      `LAVA           : ${c.lava_installed ? "found"
                          : "not found on this machine"}`,
      `output folder  : ${c.output_root || ""}`,
    ].join("\n");
    $("hex-where").textContent = "";
    $("hex-prev").disabled = true;
    $("hex-next").disabled = true;
    $("hex-modal").classList.add("open");
  } catch (e) { $("err").textContent = e.message; }
}

async function releaseImage() {
  // An examiner finishing a case wants the drive back. A run lets go by
  // itself when it finishes; this is for the times it did not, or for
  // letting go without running anything.
  try {
    const d = await api("/api/release", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: "{}"});
    // Put the page back where "nothing loaded" leaves it, so the label
    // cannot say In use after the program has let go.
    setActive("", null);
    $("btn-start").disabled = true;
    toast(d.released
      ? `Let go of <span class="p">${esc(d.released)}</span>. ${esc(d.note)}`
      : "No image was being held.");
  } catch (e) { toast(esc(e.message), true); }
}

function moveDisplayControls() {
  // The controls live on the Display tab now, put there by the ribbon as
  // it draws. This stays so anything the ribbon does not claim still has
  // somewhere to be rather than sitting in the toolbar band.
  // The toolbar had eleven filter chips and nine controls in one band.
  // The chips and the search are what an examiner touches while grading,
  // so they stay; the rest are set once a session and belong behind a
  // button. The elements are moved, not rebuilt: a second copy of a
  // control is a copy that stops working the first time somebody edits
  // the other one.
  const body = $("display-body");
  if (!body) return;
  [].forEach(id => {
    const element = $(id);
    if (!element) return;
    // Take the label or wrapper with it, so "Per page" moves with its
    // dropdown rather than leaving the words behind.
    const holder = element.closest("label.ctl, span.ctl") || element;
    if (holder.parentElement === body) return;
    const row = document.createElement("div");
    row.className = "display-row";
    row.appendChild(holder);
    body.appendChild(row);
  });
  const scope = $("blur-scope");
  if (scope && scope.parentElement && scope.parentElement !== body) {
    const row = document.createElement("div");
    row.className = "display-row";
    row.appendChild(scope);
    body.appendChild(row);
  }
}

function nudge(element, kind) {
  // Setting .value from code does not tell the page: the handlers listen
  // for the event a person typing would cause, so it has to be sent.
  try {
    element.dispatchEvent(new Event(kind, {bubbles: true}));
  } catch (e) {
    // Older engines, and the test shim.
    if (typeof element.onchange === "function") element.onchange();
  }
}

function setTileSize(px) {
  const slider = $("tile-size");
  if (!slider) return;
  const lowest = Number(slider.min) || 90;
  const highest = Number(slider.max) || 420;
  slider.value = String(Math.max(lowest, Math.min(highest, px)));
  nudge(slider, "input");
}

function stepTileSize(by) {
  const slider = $("tile-size");
  if (slider) setTileSize(Number(slider.value) + by);
}

function setPerPage(value) {
  const select = $("per-page");
  if (!select) return;
  select.value = value;
  nudge(select, "change");
}

function perPageIs(value) {
  const select = $("per-page");
  return !!select && select.value === value;
}

function cycleBlurScope() {
  // Three settings and one button: pressing it moves to the next and
  // says which, rather than opening a dialog to change one word.
  const select = $("blur-scope");
  if (!select) return;
  const options = Array.from(select.options || []);
  if (!options.length) return;
  const at = options.findIndex(o => o.value === select.value);
  const next = options[(at + 1) % options.length];
  select.value = next.value;
  nudge(select, "change");
  toast(`Blurring ${esc(next.textContent.trim())}.`);
}

function openDisplayOptions() {
  renderBlurCategories();
  $("display-modal").style.display = "flex";
}

function newExtraction() {
  // Where the form actually is, with the cursor in the box that has to be
  // filled in first.
  leaveReview();
  showTab("review");
  const box = $("path");
  if (box) {
    box.scrollIntoView({block: "center"});
    box.focus();
  }
  toast("Choose the image or folder to read, then press Load / Inspect.");
}

async function closeCase() {
  // Writes a snapshot, then puts the program back where it started: the
  // list of cases. Every decision is already on disk as it was made, so
  // the snapshot is a marker rather than a rescue -- but closing without
  // one would leave an examiner wondering, and wondering is the thing
  // worth removing.
  if (!jobId) return;
  try {
    await api("/api/case/snapshot", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, label: "closed"})});
  } catch (e) {
    if (!confirm("The case could not be saved: " + e.message +
                 "\n\nClose it anyway? Decisions already made are on disk.")) {
      return;
    }
  }
  const was = $("case-number").value.trim();
  try {
    await api("/api/cases/close", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
  } catch (e) { /* the page still closes; the handle closes at exit */ }
  claimPage();
  leaveReview();
  openedCase = null;
  jobId = null;
  files = [];
  leavePaged();
  decisions = {};
  selectedPath = "";
  setSelection([]);
  clearAllFilters();
  render();
  await loadCases();
  showTab("cases");
  toast(was ? `Closed ${esc(was)}. It is in the list below.`
            : "Case closed. It is in the list below.");
}

async function removeStoreSource(button) {
  const name = button.dataset.storeRemove;
  if (!confirm(`Remove "${name}" and all its hashes from the store? ` +
               `The original file is not touched.`)) return;
  try {
    renderStore(await api("/api/store/remove", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name})}));
  } catch (e) { $("store-err").textContent = e.message; }
}

async function importC4All() {
  // Somebody else's gradings. Shown before they are taken, because an
  // import that happens before an examiner has seen whose numbers they
  // are is not something they can later defend.
  if (!jobId) return;
  const path = prompt("Path to the C4All index (C4P Index.xml or " +
                      "C4M Index.xml):", "");
  if (!path) return;
  try {
    const d = await api("/api/c4all/inspect", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})});
    const spread = Object.entries(d.by_category || {})
      .map(([code, n]) => `${esc(String(code))}: ${count(n)}`).join(", ");
    if (!confirm(`${d.entries} entry/entries, ${d.graded} graded ` +
                 `(${spread || "none"}).\n\n` +
                 `These are the categories that index records, in whatever ` +
                 `scheme the unit that wrote it used.\n\n` +
                 `Bring them into this case? Files you have already graded ` +
                 `keep your grading.`)) return;
    // The index's codes mean something only in its own scheme; the server
    // refuses one that is not this case's.
    const mine = (caseRegion && caseRegion.id) || "US";
    const index_region = prompt(
      `Which grading scheme did the unit that wrote this index use? ` +
      `This case is graded under ${mine}; an index graded under another ` +
      `scheme is not brought in.`, mine);
    if (!index_region) return;
    const r = await api("/api/c4all/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, path,
                            index_region: index_region.trim().toUpperCase()})});
    await loadCase();
    render();
    toast(`${count(r.applied)} grading(s) brought in from ` +
          `${esc(r.index)}.` +
          (r.already_graded_here
            ? ` ${count(r.already_graded_here)} file(s) you had already ` +
              `graded kept your grading.` : "") +
          (r.disagreements && r.disagreements.length
            ? `<br><span class="warn">${r.disagreements.length} file(s) ` +
              `are graded differently there than here — yours stand.</span>`
            : ""));
  } catch (e) { toast(esc(e.message), true); }
}

async function showStash() {
  // The examiner's own gradings, kept on this machine. Shown rather than
  // hidden: a second source of categories that nobody can look at is
  // worse than no second source.
  try {
    const d = await api("/api/stash");
    const lines = Object.entries(d.by_category || {})
      .map(([code, n]) => {
        const named = categories.find(c => c.code === Number(code));
        return `${esc(named ? named.name : "code " + code)}: ${count(n)}`;
      })
      .join("<br>") || "nothing yet";
    toast(`Your stash holds ${count(d.hashes)} hash(es) from your own ` +
          `cases:<br>${lines}<br><span class="hint">${esc(d.note)}<br>` +
          `${esc(d.path)}</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function openLogo() {
  // Put on every report header unless a case sets its own. A unit's
  // reports going out with its own letterhead is not decoration: it is how
  // a prosecutor knows at a glance whose work they are reading.
  try {
    const d = await api("/api/logo");
    $("logo-state").innerHTML = d.path
      ? `Using <span class="p">${esc(d.path)}</span>` +
        (d.width ? ` — ${d.width}×${d.height}` : "")
      : "No logo set. Reports carry the agency name only.";
    $("logo-preview").innerHTML = d.path
      ? `<img src="/api/logo/image?t=${Date.now()}" alt="" ` +
        `style="max-width:260px; max-height:90px; background:#fff; ` +
        `padding:6px; border-radius:4px;">` : "";
    $("logo-path").value = d.path || "";
    $("logo-err").textContent = "";
    $("logo-modal").style.display = "flex";
  } catch (e) { toast(esc(e.message), true); }
}

// The offline basemaps the report's and the details pane's maps are drawn on.
async function showBasemaps(after) {
  try {
    const d = after || await api("/api/basemap");
    const active = (d.basemaps || []).find(b => b.active);
    $("set-basemap-state").innerHTML = active
      ? `Maps are drawn on <strong>${esc(active.name)}</strong> ` +
        `(${esc(active.format)}, zoom ${esc(active.min_zoom)}–${esc(active.max_zoom)}, ` +
        `SHA-256 ${esc(active.sha256)}).`
      : "No basemap imported: the report shows locations as dots with no map.";
  } catch (e) { $("set-basemap-state").textContent = e.message; }
}

async function importBasemap() {
  const path = $("set-basemap").value.trim();
  if (!path) return;
  $("set-basemap-state").textContent = "Importing (copying and hashing the file)…";
  try {
    showBasemaps(await api("/api/basemap", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})}));
    $("set-basemap").value = "";
  } catch (e) {
    $("set-basemap-state").innerHTML = `<span class="warn">${esc(e.message)}</span>`;
  }
}

// The regional grading schemes (fme/review/regions.py). The choice is for
// cases made from now on: an open case keeps the scheme it was created under,
// because the codes on its files mean something only in that one.
async function fillRegions(chosen) {
  const select = $("set-region");
  try {
    const d = await api("/api/regions");
    select.innerHTML = d.regions.map(r =>
      `<option value="${esc(r.id)}"${r.id === chosen ? " selected" : ""}>` +
      `${esc(r.label)}</option>`).join("");
    const show = () => {
      const r = d.regions.find(x => x.id === select.value);
      $("set-region-state").textContent = r ? r.categories.map(
        c => `${c.key ? "[" + c.key + "] " : ""}${c.code} ${c.name}`).join(" · ") : "";
    };
    select.onchange = show;
    show();
  } catch (e) { $("set-region-state").textContent = e.message; }
}

async function openSettings() {
  // What this machine remembers, so an examiner types their name once
  // rather than into every case header.
  try {
    const d = await api("/api/settings");
    $("set-examiner").value = d.examiner || "";
    $("set-agency").value = d.agency || "";
    $("set-output").value = d.output_root || "";
    await fillRegions(d.category_region || "US");
    $("set-model").value = d.content_model || "";
    $("set-model-state").textContent = "";
    // Blank is the bundled model, so the line says what a search would use.
    try {
      const m = await api("/api/content/model?path=" +
                          encodeURIComponent(d.content_model || ""));
      $("set-model-state").innerHTML = m.ready
        ? `Ready: ${esc(m.name || "model")} (${esc(m.licence || "")})` +
          `${m.bundled ? ", the one that ships with this program" : ""}.`
        : `<span class="warn">${esc(m.why)}</span>`;
    } catch (e) { /* the state line is a convenience, not the setting */ }
    showBasemaps();
    $("set-where").textContent = d.where || "";
    $("set-token").textContent = d.token_saved
      ? "A hash store token is remembered on this machine."
      : "No hash store token is stored on this machine.";
    $("set-err").textContent = "";
    $("settings-modal").style.display = "flex";
  } catch (e) { $("err").textContent = e.message; }
}

function hideScreen(on) {
  document.body.classList.toggle("hidden-screen", on);
  $("screen-hidden").style.display = on ? "block" : "none";
  // Any video that is playing is stopped as well: blurring a clip that
  // keeps running still has sound, and sound carries further than a
  // screen does.
  if (on) {
    document.querySelectorAll("video").forEach(video => {
      try { video.pause(); } catch (e) { /* nothing to do */ }
    });
  }
}

const toastHistory = [];
function toast(message, bad) {
  // A line that can be read from any tab. The exports used to write where
  // they finished into the extraction panel, which is hidden while you are
  // on another tab -- so a finished export looked like nothing happening.
  // Bottom right, with a close button and the last ten kept under
  // "Earlier": twelve seconds over the ribbon lost a long message for good.
  const bar = $("toast");
  if (!bar) return;
  bar.className = "toast" + (bad ? " bad" : "");
  $("toast-text").innerHTML = message;
  bar.style.display = "flex";
  toastHistory.unshift({at: new Date(), html: message, bad: !!bad});
  if (toastHistory.length > 10) toastHistory.pop();
  if ($("toast-log").style.display !== "none") renderToastLog();
  clearTimeout(toast._timer);
  // An error stays until it is closed; anything else goes after a while.
  if (!bad) toast._timer = setTimeout(() => { bar.style.display = "none"; }, 15000);
}

function renderToastLog() {
  $("toast-log").innerHTML = toastHistory.map(m =>
    `<div${m.bad ? ' class="warn"' : ""}><span class="when">` +
    `${m.at.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"})}` +
    `</span>${m.html}</div>`).join("") || '<span class="hint">Nothing yet.</span>';
}
$("toast-close").addEventListener("click", () => {
  $("toast").style.display = "none";
  $("toast-log").style.display = "none";
});
$("toast-history").addEventListener("click", () => {
  const log = $("toast-log");
  const open = log.style.display === "none";
  if (open) renderToastLog();
  log.style.display = open ? "block" : "none";
});

// Everything that acts like a button is reachable and pressable from the
// keyboard (v2026.11.320, U6): the ribbon's items and tabs and the filter
// chips are spans and divs, which a Tab key passed over.
const PRESSABLE = ".chip, .ribbon-item, .ribbon-tab, .panetab-like";
function makePressable(root) {
  const nodes = root.matches && root.matches(PRESSABLE) ? [root] : [];
  if (root.querySelectorAll) nodes.push(...root.querySelectorAll(PRESSABLE));
  nodes.forEach(el => {
    if (el.tagName === "BUTTON" || el.hasAttribute("tabindex")) return;
    el.setAttribute("tabindex", el.classList.contains("disabled") ? "-1" : "0");
    el.setAttribute("role", el.classList.contains("ribbon-tab") ? "tab" : "button");
    if (el.classList.contains("disabled")) el.setAttribute("aria-disabled", "true");
  });
}
makePressable(document.body);
if (typeof MutationObserver === "function") {
  new MutationObserver(records => records.forEach(r =>
    r.addedNodes.forEach(n => {
      if (n.nodeType === 1 && !n.closest("#gallery")) makePressable(n);
    }))).observe(document.body, {childList: true, subtree: true});
}
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = e.target;
  if (!el || el.tagName === "BUTTON" || el.tagName === "A" ||
      !["button", "tab"].includes(el.getAttribute("role"))) return;
  e.preventDefault();
  e.stopPropagation();
  el.click();
}, true);

async function findLikeThis(file) {
  if (!jobId || !file) return;
  toast("Describing the pictures in this case — the first search is the " +
        "slow one…");
  try {
    const d = await api("/api/content/search", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, file: file.rel_path})});
    if (!d.results.length) {
      toast("Nothing else in this case came back for that picture.");
      return;
    }
    setSelection(d.results.map(r => r.rel_path).filter(Boolean));
    const how = d.results.slice(0, 8)
      .map(r => `${esc(r.name)} — ${r.score}`).join("<br>");
    toast(`${d.results.length} picture(s), most alike first:<br>${how}` +
          `${d.results.length > 8 ? "<br>…" : ""}` +
          `<br><strong class="lead">${esc(d.lead || "Investigative lead only")}.</strong> <span class="hint">${esc(d.caveat)}</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function findFaces() {
  if (!jobId) return;
  toast("Looking for faces in this case…");
  try {
    const d = await api("/api/faces/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    toast(`${count(d.faces)} face(s) in ${count(d.with_faces)} of ` +
          `${count(d.pictures)} picture(s). Open one and use "Others like ` +
          `this face" to search.<br><strong class="lead">${esc(d.lead || "Investigative lead only")}.</strong> <span class="hint">${esc(d.caveat)}</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

// The face to search for, from those Find faces found in this picture.
async function chooseFace(sha256) {
  if (!jobId) return;
  try {
    const d = await api(`/api/faces/of?job=${jobId}&sha256=${encodeURIComponent(sha256)}`);
    if (!d.scanned) {
      toast("Find faces has not been run on this case with the face models " +
            "set now. Run it first (Case Data → Find faces).");
      return;
    }
    if (!d.faces.length) { toast("No face was found in this picture."); return; }
    let which = 0;
    if (d.faces.length > 1) {
      // Numbered left to right, as an examiner reads the picture.
      const order = d.faces.map((f, i) => [i, (f.at || [0])[0]])
        .sort((a, b) => a[1] - b[1]).map(p => p[0]);
      const said = prompt(`This picture holds ${d.faces.length} faces. Which one, ` +
                          `counting from the left (1–${d.faces.length})?`, "1");
      const n = parseInt(said || "", 10);
      if (!(n >= 1 && n <= d.faces.length)) return;
      which = order[n - 1];
    }
    await searchFace(sha256, which);
  } catch (e) { toast(esc(e.message), true); }
}

async function searchFace(sha256, which) {
  if (!jobId) return;
  try {
    const d = await api("/api/faces/search", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: sha256, face: which || 0})});
    if (!d.results.length) {
      toast("No other face in this case scored above the threshold. " +
            "That is not evidence that there is none: a different " +
            "photograph of one person can score below it.");
      return;
    }
    setSelection(d.results.map(r => r.rel_path).filter(Boolean));
    // The score is shown for every result, so a reader can see how close
    // the match actually was rather than only that it came back.
    const how = d.results.slice(0, 8).map(r =>
      `${esc(r.name)} — ${r.score}`).join("<br>");
    toast(`${d.results.length} face(s) above ${d.threshold}, now ` +
          `selected:<br>${how}${d.results.length > 8 ? "<br>…" : ""}` +
          `<br><strong class="lead">${esc(d.lead || "Investigative lead only")}.</strong> <span class="hint">${esc(d.caveat)}</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function findEditedCopies(file) {
  if (!jobId || !file) return;
  toast("Looking for edited copies — fingerprinting the case the first time…");
  try {
    const d = await api("/api/editmatch", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, file: file.rel_path})});
    if (!d.matches.length) {
      toast(`No edited copy of that picture in this case ` +
            `(${d.considered.toLocaleString()} candidate(s) matched point by ` +
            `point).`);
      return;
    }
    // Shown as a filter over the case, so they can be graded together.
    setSelection(d.matches.map(m => m.rel_path).filter(Boolean));
    const how = d.matches.map(m =>
      `${esc(m.name)} — ${m.points} point(s)` +
      (m.mirrored ? ", mirrored" : "") +
      (m.rotation ? `, turned ${m.rotation}°` : "") +
      (m.scale && Math.abs(m.scale - 1) > 0.05
        ? `, scaled ${m.scale}x` : "")).slice(0, 8).join("<br>");
    toast(`${d.matches.length} edited copy/copies of that picture, now ` +
          `selected:<br>${how}` +
          (d.matches.length > 8 ? "<br>…" : "") +
          `<br><span class="hint">Same picture, not same subject: two ` +
          `different photographs of one person are not a match here.</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function readCredentials() {
  // What each file says about itself: who made it, and whether it says it
  // was made by a model. Every major image generator writes this, so it is
  // the first thing to ask of a picture somebody has called AI-generated.
  if (!jobId) return;
  toast("Reading Content Credentials…");
  try {
    const d = await api("/api/credentials/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    await loadCase();
    render();
    toast(`${d.with_credentials.toLocaleString()} of ` +
          `${d.checked.toLocaleString()} file(s) carry Content Credentials; ` +
          `${d.say_ai_generated.toLocaleString()} state they were made by a ` +
          `trained model.` +
          (d.not_established
            ? `<br>${d.not_established.toLocaleString()} file(s) could not be ` +
              `read to the end, so "none" is not established for those.` : "") +
          `<br><span class="hint">Unverified: no signature was checked, and ` +
          `a manifest can be copied into another file.</span>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function exportAs(kind) {
  // The exports that are a file rather than a report: Project VIC and KML
  // come out of the report builder, C4All and LAVA have their own routes.
  if (!jobId) return;
  const header = {job: jobId, case: $("case-number").value.trim() ||
                  $("rp-case").value.trim()};
  try {
    if (kind === "c4all") {
      const d = await api("/api/export", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(header)});
      // The endpoint answers with what it wrote, not a folder; reading
      // the wrong key is why this said nothing at all.
      const written = (d.export && d.export.files_written) || [];
      const absent = (d.export && d.export.missing) || [];
      const stale = (d.export && d.export.stale_kept) || [];
      toast(`C4All: wrote ${written.length} file(s)` +
            (written.length ? ` — ${esc(written.join(", "))}` : "") +
            (absent.length
             ? `<br><span class="warn">${absent.length} recovered file(s) ` +
               `could not be staged; see Results.txt.</span>` : "") +
            (stale.length
             ? `<br><span class="warn">${stale.length} file(s) from an earlier ` +
               `export were left in the disc folders (moved there, so the ` +
               `only copy); they are not in the new index.</span>` : ""),
            absent.length > 0 || stale.length > 0);
      return;
    }
    if (kind === "lava") {
      const d = await api("/api/export/lava", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({...header,
                              examiner: $("case-examiner").value.trim()})});
      const lava = d.lava || {};
      toast(`LAVA: ${(lava.rows || 0).toLocaleString()} row(s) in ` +
            `${(lava.tables || []).length} table(s), ` +
            `${(lava.media_staged || 0).toLocaleString()} file(s) staged` +
            `<br><span class="p">${esc(lava.output_dir || "")}</span>` +
            ((d.problems || []).length
             ? `<br><span class="warn">${esc(d.problems.join("; "))}</span>`
             : ""),
            (d.problems || []).length > 0);
      return;
    }
    // Project VIC and KML are formats of the report, so they are asked for
    // the same way and land beside it.
    const started = await api("/api/report/build", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, formats: [kind],
                            case_number: header.case})});
    toast(`${kind === "kml" ? "KML" : "Project VIC"} export started…`);
    for (let i = 0; i < 600; i++) {
      await new Promise(r => setTimeout(r, 700));
      const d = await api(`/api/report/status?id=${started.id}`);
      if (d.status === "running") continue;
      if (d.status === "error") { toast(esc(d.error), true); return; }
      const wrote = (d.written || [])
        .map(w => w.path || w.skipped).filter(Boolean).join(", ");
      toast(`Written: <span class="p">${esc(wrote)}</span>`);
      return;
    }
  } catch (e) { toast(esc(e.message), true); }
}

function stashControls() {
  // Somewhere off-screen for a control to live while it is not on the
  // ribbon. It keeps working from here: the page looks controls up by
  // id, and reads and writes them whether or not they are visible.
  // Built here rather than in the page, and so reached defensively:
  // the first call makes it.
  let stash = $("control-stash");
  if (!stash) {
    stash = document.createElement("div");
    stash.id = "control-stash";
    stash.style.display = "none";
    document.body.appendChild(stash);
  }
  document.querySelectorAll("#ribbon-groups [data-control]")
    .forEach(holder => {
      const element = $(holder.dataset.control);
      if (element) stash.appendChild(element.closest("label.ctl") || element);
    });
}

function homeDisplayControls() {
  // Every control the ribbon carries lives on the ribbon's Display tab, from
  // the start. They used to sit in the toolbar band until the Display tab was
  // first opened and then vanish from it for good, so the toolbar changed
  // under an examiner who had only looked at a tab (v2026.11.320, U9).
  stashControls();
  const stash = $("control-stash");
  ["gray-on", "blur-on"].forEach(id => {
    const element = $(id);
    if (element && !element.closest("#ribbon-groups"))
      stash.appendChild(element.closest("label.ctl, span.ctl") || element);
  });
  Object.values(ribbonDefinition()).forEach(groups => groups.forEach(group =>
    group.items.forEach(item => {
      if (!item.control) return;
      const element = $(item.control);
      if (!element || element.closest("#ribbon-groups")) return;
      stash.appendChild(element.closest("label.ctl, span.ctl") || element);
    })));
}

function renderRibbon() {
  const definition = ribbonDefinition();
  const names = Object.keys(definition);
  const tabs = $("ribbon-tabs");
  if (!tabs) return;
  tabs.innerHTML = names.map(name =>
    `<div class="ribbon-tab${name === ribbonTab ? " active" : ""}" ` +
    `data-ribbon="${esc(name)}">${esc(name)}</div>`).join("");
  tabs.querySelectorAll("[data-ribbon]").forEach(tab =>
    tab.addEventListener("click", () => {
      ribbonTab = tab.dataset.ribbon;
      renderRibbon();
    }));

  const groups = definition[ribbonTab] || [];
  // Rescue the real controls before the ribbon is redrawn. Setting
  // innerHTML destroys whatever is inside, and these are the page's only
  // copies -- switching away from the Display tab threw away the tile
  // slider, both dropdowns and the toggles, and everything that looked
  // them up afterwards got null.
  stashControls();

  $("ribbon-groups").innerHTML = groups.map((group, groupIndex) =>
    `<div class="ribbon-group"><div class="ribbon-items">` +
    group.items.map((item, index) => {
      const off = item.needs === false;
      const lit = typeof item.on === "function" && item.on();
      // An item can be a control rather than a button. The control is
      // the one the page already has -- moved into place below, not
      // rebuilt -- so a dropdown on the ribbon and the setting it
      // changes cannot drift apart.
      if (item.control) {
        return `<div class="ribbon-control" data-control="${item.control}" ` +
               `title="${esc(item.tip || item.label)}">` +
               `<span class="ribbon-control-label">${esc(item.label)}</span>` +
               `<span class="ribbon-control-slot"></span></div>`;
      }
      return `<div class="ribbon-item${off ? " disabled" : ""}` +
        `${lit ? " on" : ""}" data-go="${groupIndex}:${index}" ` +
        // The hover text says what the thing does. Repeating the label
        // there told an examiner nothing they could not already read.
        `title="${esc(item.tip || item.label)}">` +
        `<span class="ribbon-glyph">${item.glyph}</span>` +
        `<span>${esc(item.label)}</span></div>`;
    }).join("") +
    `</div><div class="ribbon-group-name">${esc(group.name)}</div></div>`
  ).join("");

  // Put the real controls into their slots. appendChild moves rather
  // than copies, so each control exists once however often the ribbon is
  // redrawn.
  $("ribbon-groups").querySelectorAll("[data-control]").forEach(holder => {
    const element = $(holder.dataset.control);
    if (!element) return;
    const slot = holder.querySelector(".ribbon-control-slot");
    if (slot) slot.appendChild(element.closest("label.ctl") || element);
  });

  $("ribbon-groups").querySelectorAll("[data-go]").forEach(element => {
    const [groupIndex, index] = element.dataset.go.split(":").map(Number);
    const item = groups[groupIndex].items[index];
    if (item.needs === false) return;
    element.addEventListener("click", async () => {
      // Most items are async. A try around a call that returns a promise
      // catches nothing the promise later rejects with, so a failed action
      // said nothing at all -- the console had it, the examiner did not.
      // Redrawn at once for toggles, and again when an action finishes.
      let running;
      try { running = item.go(); } catch (e) { toast(esc(e.message), true); }
      renderRibbon();
      try { await running; } catch (e) { toast(esc(e.message), true); }
      renderRibbon();
    });
  });
}

$("cases-refresh").addEventListener("click", loadCases);
$("cases-new").addEventListener("click", newExtraction);
$("cases-scan-browse").addEventListener("click", () =>
  openBrowser(p => { $("cases-scan-path").value = p; },
              $("cases-scan-path").value.trim(), "Choose a folder to scan"));
$("cases-scan").addEventListener("click", async () => {
  $("cases-err").textContent = "";
  const path = $("cases-scan-path").value.trim();
  if (!path) { $("cases-err").textContent = "Choose a folder first."; return; }
  $("cases-scan").disabled = true;
  $("cases-scan").textContent = "Scanning…";
  try {
    const d = await api("/api/cases/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})});
    await loadCases();
    const moved = (d.cases || []).filter(c => c.relinked_from);
    $("cases-err").textContent = d.found
      ? "" : "No output folders found under there.";
    if (moved.length) {
      $("status").textContent =
        `${moved.length} case(s) had moved and now point at their new place`;
    }
  } catch (e) { $("cases-err").textContent = e.message; }
  finally { $("cases-scan").disabled = false; $("cases-scan").textContent = "Scan"; }
});

loadCases();

// ---- blurring -----------------------------------------------------------
// An examiner reviewing this material often has somebody else in the room,
// or a screen that faces a door. Blurring makes a page safe to have open; it
// is not a safeguard, and the picture is one hover away, which is the point.
// A machine that has never been asked blurs anything not yet graded: what
// nobody has looked at is the part most likely to be hard to look at.
let blurScope = recall("blurScope", "ungraded");
// Which categories are blurred when the scope is "the categories chosen".
let blurCategories = new Set(JSON.parse(recall("blurCats", "[1,2,3,9]")));

function blurOn() { return $("blur-on").checked; }

function shouldBlur(f) {
  if (!blurOn() || f.category !== "image" && f.category !== "video") return false;
  const decided = decisions[f.sha256];
  const code = decided ? decided.category : uncategorizedCode;
  if (blurScope === "all") return true;
  if (blurScope === "ungraded") return code === uncategorizedCode;
  return blurCategories.has(code);
}

$("blur-on").addEventListener("change", () => {
  remember("blur", $("blur-on").checked ? "1" : "0");
  render();
  if (selectedPath && pane === "details") showDetails(selectedPath);
});
$("blur-scope").addEventListener("change", () => {
  blurScope = $("blur-scope").value;
  remember("blurScope", blurScope);
  render();
  if (selectedPath && pane === "details") showDetails(selectedPath);
});
$("blur-on").checked = recall("blur", "0") === "1";
$("blur-scope").value = blurScope;

// Which categories "the categories ticked" means, chosen per scheme, since
// the codes mean different things in each (v2026.11.320, U8). Until a
// choice is made, the scheme's own notable categories.
function blurChoiceFor(region) {
  const id = (region && region.id) || "US";
  const fallback = id === "US" ? [1, 2, 3, 9] : ((region && region.notable) || []);
  try {
    const kept = recall("blurCats." + id, id === "US" ? recall("blurCats", "") : "");
    return kept ? JSON.parse(kept) : fallback;
  } catch (e) { return fallback; }
}

function renderBlurCategories() {
  const box = $("blur-cats");
  if (!box) return;
  box.innerHTML = categories.map(c =>
    `<label><input type="checkbox" data-blurcat="${c.code}"` +
    `${blurCategories.has(c.code) ? " checked" : ""}>` +
    `<span style="color:${esc(c.color || "")}">■</span> ${esc(c.name)}</label>`).join("") ||
    '<span class="hint">Open a case to choose from its categories.</span>';
  box.querySelectorAll("[data-blurcat]").forEach(input =>
    input.addEventListener("change", () => {
      const code = Number(input.dataset.blurcat);
      input.checked ? blurCategories.add(code) : blurCategories.delete(code);
      remember("blurCats." + ((caseRegion && caseRegion.id) || "US"),
               JSON.stringify([...blurCategories]));
      if (blurScope !== "graded") {
        $("blur-scope").value = "graded";
        nudge($("blur-scope"), "change");
      } else { render(); }
    }));
}

function askWellbeingOnce() {
  if (recall("wellbeingAsked", "") === "1") return;
  $("wellbeing-card").style.display = "block";
}
$("wellbeing-ok").addEventListener("click", () => {
  const picked = (document.querySelector('#wellbeing-card input[name="wb"]:checked') || {}).value
    || "ungraded";
  if (picked === "none") {
    $("blur-on").checked = false;
  } else {
    $("blur-on").checked = true;
    $("blur-scope").value = picked;
    blurScope = picked;
    remember("blurScope", picked);
  }
  remember("blur", $("blur-on").checked ? "1" : "0");
  if ($("wb-gray").checked !== $("gray-on").checked) $("gray-on").click();
  remember("wellbeingAsked", "1");
  $("wellbeing-card").style.display = "none";
  toast(picked === "none" ? "Showing pictures clearly. The Display tab changes this."
        : "Blur set. The Display tab changes this; hold B to see a file clearly.");
  render();
});

// B shows the selected file clearly for as long as it is held, which is how
// a blurred page is actually worked: glance, decide, move on.
document.addEventListener("keydown", e => {
  if (e.key !== "b" && e.key !== "B") return;
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  document.querySelectorAll(".card.blurred, .preview.blurred")
    .forEach(el => el.classList.add("shown"));
});
document.addEventListener("keyup", e => {
  if (e.key !== "b" && e.key !== "B") return;
  document.querySelectorAll(".shown").forEach(el => el.classList.remove("shown"));
});

// ---- grading from the keyboard -----------------------------------------
// The whole review runs through this: a key per category, a key per flag,
// and the cursor moving on by itself. Everything else in the interface is a
// way of deciding what to look at; this is the part that does the work.
// The dialog in front, if any. The full size viewer is left out: it shows
// the file being graded, and grading from it is how it is used.
function openDialog() {
  return [...document.querySelectorAll(".modal-overlay")].find(m =>
    m.id !== "full-modal" && getComputedStyle(m).display !== "none") || null;
}

// Escape closes the dialog in front, as it does everywhere else. Only Close
// or Save did, so a dialog opened by mistake had to be hunted for a button.
function closeFullSize() {
  $("full-modal").classList.remove("open");
  zoomReset();
}

document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  // The full size viewer is left out of openDialog() so grading keys reach
  // the file it shows -- and that left Escape nothing to close. It fell
  // through to the gallery and cleared the selection, so the viewer stayed
  // open on a file no longer selected and the next grading key did nothing.
  // Escape is not a grading key: it closes the viewer, and the file stays
  // selected. (tests/test_browser_dialog_keys.py)
  if ($("full-modal").classList.contains("open") && !openDialog()) {
    e.preventDefault();
    e.stopImmediatePropagation();
    closeFullSize();
    return;
  }
  const dialog = openDialog();
  if (!dialog) return;
  e.preventDefault();
  e.stopImmediatePropagation();     // and does not also clear the selection
  // Closed the way it was opened: some dialogs show by a class, some by
  // style. An inline "none" on a class-shown one would stop it reopening.
  if (dialog.classList.contains("open")) dialog.classList.remove("open");
  else dialog.style.display = "none";
});

document.addEventListener("keydown", e => {
  // Typing into any field -- a dropdown included -- is not grading.
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)
      || e.target.isContentEditable) return;
  // Nor is a key pressed with a dialog in front. The keys went through to
  // the gallery behind it: a digit with a dialog's button focused graded
  // the hidden file, Ctrl-Z undid the last grading, Ctrl-A selected a page.
  if (openDialog()) return;

  // Ctrl-B hides the screen from the room.
  //
  // Deliberately not the same thing as the blur used while reviewing. That
  // one is per category and lifts when the pointer rests on a tile, which
  // is how a blurred page is worked. Neither is any use when somebody
  // walks up to the desk: an examiner sitting in Uncategorized has, by
  // definition, files nobody has looked at yet, and the review blur leaves
  // those clear.
  //
  // So this hides every picture and video on the page, whatever its
  // category, lifts for nothing, and needs one hand. It changes no file,
  // no decision and nothing in a report.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    hideScreen(!document.body.classList.contains("hidden-screen"));
    return;
  }

  // Zooming, while the full size viewer is open. 0 and 1 zoom only when
  // they grade nothing in this case: in a US case they are Non-Pertinent
  // and CAM, and taking them for zoom made those grades silently do nothing
  // in the one view where the picture is seen whole.
  if ($("full-modal").classList.contains("open")) {
    const grades = k => categories.some(c => c.key === k) ||
                        caseFlags.some(f => f.key === k);
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomStep(1.4); return; }
    if (e.key === "-" || e.key === "_") {
      if (!grades(e.key)) { e.preventDefault(); zoomStep(1 / 1.4); return; }
    }
    if (e.key === "0" && !grades("0")) { e.preventDefault(); zoomReset(); return; }
    if (e.key === "1" && !grades("1")) { e.preventDefault(); zoomTo(1); return; }
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && caseSize()) {
    e.preventDefault();
    // The page, not the whole case. Ctrl-A in a gallery means "everything
    // I can see" to anyone who has used one, and the difference between
    // five hundred files and a hundred and sixty thousand is a morning's
    // work either way. Selecting the whole filter is a button, which says
    // how many it is selecting.
    setSelection(pageOf(visible()).map(f => f.rel_path));
    $("status").textContent =
      `selected the ${selection.size.toLocaleString()} file(s) on this page — ` +
      `use "Select all matching" for everything the filter shows`;
    return;
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    undoLast();
    return;
  }
  if (e.key === "Escape" && selection.size) { setSelection([]); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // Moving and opening, from the keyboard (v2026.11.320, U4). Not on a
  // focused button or link, where Enter is that control's own.
  const onControl = ["BUTTON", "A"].includes(e.target.tagName) ||
                    e.target.getAttribute("role") === "button";
  const viewerOpen = $("full-modal").classList.contains("open");
  const grading = k => categories.some(c => c.key === k) ||
                       caseFlags.some(f => f.key === k);
  const tileHere = e.target.closest && e.target.closest("[data-pick]");
  if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && caseSize() &&
      (viewerOpen || (selectedPath && (!onControl || tileHere)))) {
    e.preventDefault();
    stepFile(e.key === "ArrowRight" ? 1 : -1);
    return;
  }
  if (e.key === "Enter" && !viewerOpen && (selectedPath || tileHere) &&
      (!onControl || tileHere)) {
    const picked = lookup(tileHere ? tileHere.dataset.pick : selectedPath);
    if (picked) { e.preventDefault(); viewFullSize(picked); return; }
  }
  if (viewerOpen && (e.key === "m" || e.key === "M") && !grading(e.key)) {
    e.preventDefault();
    setMasking(!masking);
    return;
  }

  // A selection is graded whole; otherwise the file under the cursor is.
  // After "Select all matching" nothing has been clicked, so fall back to
  // the first file of the selection. With the full size viewer open, the
  // file graded is the one it shows -- never one the examiner cannot see.
  const viewing = $("full-modal").classList.contains("open") ? fullFile : null;
  // A selection made before the viewer opened is not what is on screen.
  if (viewing && selection.size > 1) setSelection([viewing.rel_path]);
  const file = viewing || lookup(selectedPath)
            || (paged ? pv.files.find(f => selection.has(f.rel_path))
                      : files.find(f => selection.has(f.rel_path)));
  if (!file) return;

  const category = categories.find(c => c.key && c.key === e.key);
  if (category) {
    e.preventDefault();
    categorize(file.sha256, category.code);
    return;
  }
  // A flag key toggles and leaves the cursor where it is: a file has one
  // grading and any number of flags.
  const flag = caseFlags.find(f => f.key && f.key === e.key);
  if (flag) {
    e.preventDefault();
    toggleFlag(file.sha256, flag.id);
  }
});

// ---- the three panes ---------------------------------------------------
// One column, three things it can show. Which one is open is a display
// choice; what is filtered is not, so the two are kept apart.
let pane = "details";

// Facets filter on the file's own metadata. Several values inside one facet
// mean "any of these"; values in different facets all have to hold, which is
// how a question like "this camera, in this city" gets asked.
const SIZE_BANDS = [
  [0, 100 * 1024, "under 100 KB"],
  [100 * 1024, 1024 * 1024, "100 KB – 1 MB"],
  [1024 * 1024, 10 * 1024 * 1024, "1 – 10 MB"],
  [10 * 1024 * 1024, 100 * 1024 * 1024, "10 – 100 MB"],
  [100 * 1024 * 1024, 1024 * 1024 * 1024, "100 MB – 1 GB"],
  [1024 * 1024 * 1024, Infinity, "over 1 GB"],
];

function year(value) {
  // The year a time falls in where the examiner is looking from -- the zone
  // the details pane converts to. A video's capture time is UTC (MP4), so a
  // clip taken at 9 PM on Dec 31 in Philadelphia is 2025 by its first four
  // characters, and filtering to 2024 hid it. A time with no zone is the
  // device's clock and keeps the year it recorded. galleryquery.year in
  // the server must give the same answer (tests/test_gallery_parity.py).
  if (!value || value.length < 4) return null;
  const text = String(value);
  if (/[zZ]$|[+-]\d\d:?\d\d$/.test(text)) {
    const when = new Date(text);
    if (!isNaN(when.getTime())) {
      try {
        return new Intl.DateTimeFormat("en-CA",
          {timeZone: timeZone, year: "numeric"}).format(when);
      } catch (e) { return String(when.getUTCFullYear()); }
    }
  }
  return text.slice(0, 4);
}

function hasExif(f) {
  // Any of the things only metadata supplies. The file system knows a name
  // and a size; it does not know a camera or when a shutter opened.
  return !!(f.captured || f.camera_make || f.camera_model || f.artist ||
            f.body_serial_present || f.city ||
            (f.latitude !== null && f.latitude !== undefined));
}

// A facet turns a file into the value or values it should be listed under.
// Returning a list puts the file in several at once, which is how "has GPS"
// and "has EXIF" can both be true of the same photograph.
const FACETS = [
  {key: "file_type", label: "File type", order: "fixed", value: f => {
    const out = [];
    if (f.category === "image") out.push(f.animated ? "Animated image" : "Still image");
    else if (f.category === "video") out.push("Video");
    else if (f.category === "audio") out.push("Audio");
    else out.push("Other");
    return out;
  }},
  {key: "ext", label: "File extension", value: f => (f.ext || "").toLowerCase()},
  // What the screening pass measured. Named for what it is: the proportion
  // of the picture in a skin-tone band, not a judgement about the picture.
  {key: "skin_band", label: "Skin-tone proportion", order: "fixed",
   value: f => f.skin_band || null},
  {key: "faces", label: "Faces found", order: "fixed", value: f => {
    if (f.faces === undefined || f.faces === null || f.faces < 0) return null;
    return f.faces === 0 ? "none found" : (f.faces === 1 ? "one" : "more than one");
  }},
  {key: "size_band", label: "File size", order: "fixed", value: f => {
    const band = SIZE_BANDS.find(([low, high]) => f.size >= low && f.size < high);
    return band ? band[2] : null;
  }},
  {key: "deleted", label: "Deleted", order: "fixed",
   value: f => f.deleted ? "Deleted" : "Not deleted"},
  {key: "metadata", label: "Metadata", order: "fixed", value: f => {
    const out = [hasExif(f) ? "Has metadata" : "No metadata"];
    if (f.latitude !== null && f.latitude !== undefined) out.push("Has GPS");
    return out;
  }},
  {key: "captured_year", label: "Date taken (EXIF)", order: "value",
   value: f => year(f.captured)},
  {key: "crtime_year", label: "File created", order: "value",
   value: f => year(f.crtime)},
  {key: "mtime_year", label: "File last written", order: "value",
   value: f => year(f.mtime)},
  {key: "atime_year", label: "File last accessed", order: "value",
   value: f => year(f.atime)},
  {key: "artist", label: "Camera author", value: f => f.artist},
  {key: "camera_make", label: "Camera make", value: f => f.camera_make},
  {key: "camera_model", label: "Camera model", value: f => f.camera_model},
  {key: "body_serial", label: "Camera serial number", value: f => f.body_serial},
  {key: "city", label: "City", value: f => f.city},
  {key: "state", label: "State or province", value: f => f.state},
  {key: "country", label: "Country", value: f => f.country},
];

// Fixed-order facets read in a sensible sequence rather than by popularity:
// a size band after a larger one, or "Not deleted" above "Deleted", is
// harder to scan.
const FIXED_ORDER = {
  skin_band: ["Very little skin tone", "Low", "Low–medium", "Medium",
              "Medium–high", "High", "Could not be read"],
  faces: ["none found", "one", "more than one"],
  file_type: ["Still image", "Animated image", "Video", "Audio", "Other"],
  size_band: SIZE_BANDS.map(b => b[2]),
  deleted: ["Not deleted", "Deleted"],
  metadata: ["Has metadata", "Has GPS", "No metadata"],
};

function facetValues(facet, f) {
  const value = facet.value(f);
  if (value === null || value === undefined || value === "") return [];
  return Array.isArray(value) ? value.filter(Boolean) : [value];
}

let facetFilter = {};     // key -> Set of chosen values
let flagFilter = new Set();

function showPane(which) {
  pane = which;
  if (typeof renderRibbon === "function" && !renderingRibbon) {
    renderingRibbon = true;
    try { renderRibbon(); } finally { renderingRibbon = false; }
  }
  detailsOpen = true;
  $("details-col").style.display = "block";
  $("wrap").classList.add("with-details");
  ["details", "classify", "filters"].forEach(name =>
    $(name).style.display = name === which ? "block" : "none");
  ["btn-details", "btn-classify", "btn-filters"].forEach(id =>
    $(id).classList.toggle("active", id === "btn-" +
      {details: "details", classify: "classify", filters: "filters"}[which]));
  $("btn-pane").classList.add("active");
  if (which === "details" && selectedPath) showDetails(selectedPath);
  if (which === "classify") renderClassification();
  if (which === "filters") renderFilters();
}

// A tab switches what the pane shows; it never closes it. Closing is the ✕
// and the toolbar's one control, which is where someone looks for it.
$("btn-classify").addEventListener("click", () => showPane("classify"));
$("btn-filters").addEventListener("click", () => showPane("filters"));

$("btn-undo").addEventListener("click", undoLast);

$("btn-pane").addEventListener("click", () => {
  if (detailsOpen) { setDetailsVisible(false); return; }
  showPane(pane);
  if (pane === "details" && !selectedPath) $("details").innerHTML =
    '<p class="hint" style="margin:0;">Click a file to see its details.</p>';
});

// ---- classification: what has been decided, and a way into it ----------
function renderClassification() {
  // An empty pane looks like a broken program. Each of these says which
  // of the three possible reasons it is, because "nothing here" sent an
  // examiner looking for a scheme to set up that was already there.
  if (!jobId) {
    $("classify").innerHTML =
      '<p class="hint" style="margin:0;">No case is open. Open one from ' +
      'the Cases tab, or start a new extraction, and the categories ' +
      'appear here.</p>';
    return;
  }
  if (!caseSize()) {
    $("classify").innerHTML =
      '<p class="hint" style="margin:0;">This case has no files yet. If a ' +
      'run is still going, the categories appear as soon as it has ' +
      'recovered something.</p>';
    return;
  }
  if (!categories.length && running) {
    // A run's case is being made: its categories arrive within seconds
    // (poll asks for them). Saying the case "could not be read" and that
    // its folder "may have moved" -- which this pane did through the whole
    // of every run -- sent examiners looking for a fault that was not there.
    $("classify").innerHTML =
      '<p class="hint" style="margin:0;">Loading this case\'s categories ' +
      'and tags… they appear within a few seconds of a run starting.</p>';
    return;
  }
  if (!categories.length) {
    // Not a thing an examiner sets up: every case is seeded with the
    // scheme. An empty list means the case file did not load, which is
    // worth saying rather than leaving a blank pane.
    $("classify").innerHTML =
      '<p class="hint" style="margin:0;">The categories for this case ' +
      'could not be read. They are not something you set up — every case ' +
      'is created with the scheme already in it — so this means the ' +
      "case's own file did not load. Press Refresh on the Home tab, and " +
      'if it stays empty the case folder may have moved.</p>';
    return;
  }
  // Counts respect everything except the facet being counted, so the numbers
  // are what clicking would actually give.
  // A paged case's counts are the server's, made the same way
  // (galleryquery.aggregates).
  const served = paged ? ((pv.counts || {}).classify || {by_category: {}, flags: {}}) : null;
  const base = paged ? [] : files.filter(f => passesBase(f));
  const inCategory = code => served ? (served.by_category[code] || 0)
                                    : base.filter(f => categoryOf(f) === code).length;

  const catRows = categories.map(c =>
    `<div class="v ${String(c.code) === catFilter ? "on" : ""}" ` +
    `data-class-cat="${c.code}" style="border-left:3px solid ${esc(c.color)}">` +
    `<span>${c.key ? `<span class="key">${esc(c.key)}</span> ` : ""}` +
    `${esc(c.name)}</span>` +
    `<span class="n">${inCategory(c.code).toLocaleString()}</span></div>`).join("");

  // Tags are counted within whatever category is showing, because "category 1
  // with the AI tag" is the question being asked.
  const scope = catFilter === "" ? base
              : base.filter(f => String(categoryOf(f)) === catFilter);
  const flagRows = caseFlags.map(flag => {
    const n = served ? (served.flags[flag.id] || 0)
      : scope.filter(f => (decisions[f.sha256] || {}).flags &&
                          decisions[f.sha256].flags.indexOf(flag.id) >= 0).length;
    return `<div class="v ${flagFilter.has(flag.id) ? "on" : ""}" ` +
      `data-class-flag="${flag.id}" style="border-left:3px solid ${esc(flag.color)}">` +
      `<span>${esc(flag.name)}</span><span class="n">${n.toLocaleString()}</span></div>`;
  }).join("");

  $("classify").innerHTML =
    `<div class="facet"><h4>Categories</h4><div class="values">${catRows}</div></div>` +
    `<div class="facet"><h4>Tags${catFilter === "" ? ""
      : ` <span class="hint">within ${esc((categories.find(c =>
          String(c.code) === catFilter) || {}).name || "")}</span>`}</h4>` +
    `<div class="values">${flagRows || '<span class="none">No tags in this case.</span>'}</div></div>` +
    // The examiner's own categories. /api/case/category could add, rename
    // and delete them, and the manual sent examiners to a "Categories"
    // screen for it -- but nothing on the page called it, and the ribbon
    // item of that name opened Custom Signatures.
    `<div class="facet"><h4>Your own categories</h4>` +
    categories.filter(c => !c.locked).map(c =>
      `<div class="row" style="gap:4px;align-items:center;margin:2px 0;">` +
      `<span style="flex:1;border-left:3px solid ${esc(c.color)};padding-left:6px;">` +
      `${esc(c.name)} <span class="hint">(${c.code})</span></span>` +
      `<button class="secondary" data-cat-rename="${c.code}">Rename</button>` +
      `<button class="secondary" data-cat-delete="${c.code}">Delete</button></div>`).join("") +
    // A form, so Enter adds without a key handler of its own; the grading
    // keys already ignore what is typed into a field.
    `<form id="cat-new" class="row" style="gap:4px;margin-top:4px;">` +
    `<input id="cat-new-name" placeholder="New category name" style="flex:1;">` +
    `<button class="secondary" type="submit">Add</button></form>` +
    `<p class="hint" style="margin:4px 0 0;">The ten scheme categories cannot be ` +
    `renamed or removed: a report that cites category 1 must mean the same ` +
    `thing in every case.</p></div>`;

  const changeCategory = async body => {
    try {
      const d = await api("/api/case/category", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({job: jobId, ...body})});
      categories = d.categories;
      caseCounts = d.counts || caseCounts;
      render(); renderClassification();
    } catch (e) { toast(esc(e.message), true); }
  };
  // Built just above, inside the pane, so looked up there rather than by a
  // page-wide id the page itself does not hold.
  const pane_ = $("classify");
  pane_.querySelector("#cat-new").addEventListener("submit", e => {
    e.preventDefault();
    const name = (pane_.querySelector("#cat-new-name").value || "").trim();
    if (name) changeCategory({create: name});
  });
  $("classify").querySelectorAll("[data-cat-rename]").forEach(b =>
    b.addEventListener("click", () => {
      const code = parseInt(b.dataset.catRename, 10);
      const was = (categories.find(c => c.code === code) || {}).name || "";
      const name = prompt("New name for this category:", was);
      if (name && name.trim() && name.trim() !== was)
        changeCategory({code, rename: name.trim()});
    }));
  $("classify").querySelectorAll("[data-cat-delete]").forEach(b =>
    b.addEventListener("click", () => {
      const code = parseInt(b.dataset.catDelete, 10);
      const name = (categories.find(c => c.code === code) || {}).name || code;
      if (confirm(`Delete "${name}"? Files graded with it become uncategorized. ` +
                  `The audit log keeps the change.`))
        changeCategory({delete: code});
    }));

  $("classify").querySelectorAll("[data-class-cat]").forEach(el =>
    el.addEventListener("click", () => {
      const code = el.dataset.classCat;
      catFilter = (code === catFilter) ? "" : code;
      page = 0; render(); renderClassification();
    }));
  $("classify").querySelectorAll("[data-class-flag]").forEach(el =>
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.classFlag, 10);
      flagFilter.has(id) ? flagFilter.delete(id) : flagFilter.add(id);
      page = 0; render(); renderClassification();
    }));
}

function categoryOf(f) {
  const d = decisions[f.sha256];
  return d ? d.category : uncategorizedCode;
}

// ---- filters: the file's own metadata, as facets -----------------------
function renderFilters() {
  if (!caseSize()) {
    $("filters").innerHTML = '<p class="hint" style="margin:0;">Nothing extracted yet.</p>';
    return;
  }
  if (paged && !pv.facets) {
    // Counted on the server, on the next page asked for.
    // Said with its size: in a case of two hundred thousand files the count
    // takes a while, and a bare "Counting…" read as stuck (v2026.11.323).
    $("filters").innerHTML = '<p class="hint" style="margin:0;">Counting the ' +
      `values of ${caseSize().toLocaleString()} files — in a case this large ` +
      'that can take a minute; the gallery works meanwhile.</p>';
    refreshCounts();
    return;
  }
  const blocks = FACETS.map(facet => {
    // Count each value against everything else that is filtering, so the
    // numbers describe the result of clicking rather than the whole run.
    let counts = {};
    let missing = 0;
    if (paged) {
      const served = pv.facets[facet.key] || {counts: {}, missing: 0};
      counts = Object.assign({}, served.counts);
      missing = served.missing || 0;
    } else {
      files.filter(f => passesAll(f, facet.key)).forEach(f => {
        const values = facetValues(facet, f);
        if (!values.length) { missing += 1; return; }
        values.forEach(v => { counts[v] = (counts[v] || 0) + 1; });
      });
    }

    const chosen = facetFilter[facet.key] || new Set();
    let values = Object.entries(counts);
    if (facet.order === "fixed") {
      const order = FIXED_ORDER[facet.key] || [];
      values.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
    } else if (facet.order === "value") {
      values.sort((a, b) => b[0].localeCompare(a[0]));   // newest year first
    } else {
      values.sort((a, b) => b[1] - a[1]);
      values = values.slice(0, 300);
    }

    if (!values.length && !missing) {
      return `<div class="facet"><h4>${esc(facet.label)}</h4>` +
        `<span class="none">No file carries this.</span></div>`;
    }
    // Long, open-ended lists scroll; short ones are shown whole.
    const long = values.length + (missing ? 1 : 0) > 14;
    return `<div class="facet${long ? " scrolls" : ""}">` +
      `<h4>${esc(facet.label)}</h4><div class="values">` +
      values.map(([value, n]) =>
        `<div class="v ${chosen.has(value) ? "on" : ""}" ` +
        `data-facet="${esc(facet.key)}" data-value="${esc(value)}">` +
        `<span>${esc(value)}</span><span class="n">${n.toLocaleString()}</span></div>`
      ).join("") +
      (missing ? `<div class="v ${chosen.has("\u0000") ? "on" : ""}" ` +
        `data-facet="${esc(facet.key)}" data-value="&#0;">` +
        `<span class="none">(not recorded)</span>` +
        `<span class="n">${missing.toLocaleString()}</span></div>` : "") +
      `</div></div>`;
  }).join("");

  $("filters").innerHTML =
    `<p class="hint" style="margin-top:0;">From each file's own metadata and ` +
    `the file system. Several values in one list mean any of them; different ` +
    `lists all have to hold.</p>` + blocks +
    `<p class="hint"><strong>Skin-tone proportion</strong> is how much of a ` +
    `picture sits in a broad skin-tone color band — arithmetic over pixels, ` +
    `not a judgement about what the picture shows. Sand, wood, brick and a ` +
    `close-up of a face all score high; a clothed subject, a dark photograph ` +
    `and a small subject in a large frame all score low. Use it to decide ` +
    `what to look at first. <strong>A low score is not "cleared".</strong> ` +
    `Run it from the Hash Sets tab.</p>`;

  $("filters").querySelectorAll("[data-facet]").forEach(el =>
    el.addEventListener("click", () => {
      const key = el.dataset.facet, value = el.dataset.value;
      const set = facetFilter[key] || (facetFilter[key] = new Set());
      set.has(value) ? set.delete(value) : set.add(value);
      if (!set.size) delete facetFilter[key];
      page = 0; render(); renderFilters();
    }));
}

// ---- what is filtering, and the way out --------------------------------
function activeFilters() {
  const out = [];
  if (hideKnownSoftware) out.push({
    label: "known software hidden",
    clear: () => {
      hideKnownSoftware = false;
      $("chip-nsrl").classList.remove("active");
    }});
  if (showContainers) out.push({
    label: "containers shown",
    clear: () => {
      showContainers = false;
      $("chip-containers").classList.remove("active");
    }});
  if (filter !== "all") out.push({label: `type: ${filter}`, clear: () => {
    filter = "all";
    document.querySelectorAll(".chip[data-filter]").forEach(c =>
      c.classList.toggle("active", c.dataset.filter === "all"));
  }});
  if (catFilter !== "") {
    const c = categories.find(x => String(x.code) === catFilter);
    out.push({label: `category: ${c ? c.name : catFilter}`,
              clear: () => { catFilter = ""; }});
  }
  flagFilter.forEach(id => {
    const flag = caseFlags.find(f => f.id === id);
    out.push({label: `tag: ${flag ? flag.name : id}`,
              clear: () => { flagFilter.delete(id); }});
  });
  Object.entries(facetFilter).forEach(([key, set]) => {
    const facet = FACETS.find(f => f.key === key);
    set.forEach(value => out.push({
      label: `${facet ? facet.label : key}: ` +
             (value === "\u0000" ? "(not recorded)" : value),
      clear: () => {
        set.delete(value);
        if (!set.size) delete facetFilter[key];
      }}));
  });
  const query = ($("search").value || "").trim();
  if (query) out.push({label: `search: ${query}`,
                       clear: () => { $("search").value = ""; }});
  if (similarOnly) out.push({label: `similar images only`,
                             clear: () => { similarOnly = null; }});
  return out;
}

function renderActiveFilters(shown, total) {
  const active = activeFilters();
  if (!active.length) { $("active-filters").style.display = "none"; return; }
  $("active-filters").style.display = "flex";
  $("active-filters").innerHTML =
    `<span class="hint">Showing <strong>${shown.toLocaleString()}</strong> of ` +
    `${total.toLocaleString()}:</span>` +
    active.map((f, i) => `<span class="f">${esc(f.label)}` +
      `<span class="x" data-clear="${i}" title="Remove this filter">✕</span></span>`).join("") +
    `<button class="secondary" id="clear-filters" style="padding:2px 10px;">` +
    `Clear all filters</button>`;
  $("active-filters").querySelectorAll("[data-clear]").forEach(x =>
    x.addEventListener("click", () => {
      active[parseInt(x.dataset.clear, 10)].clear();
      page = 0; render();
      if (pane === "classify") renderClassification();
      if (pane === "filters") renderFilters();
    }));
  const clearButton = $("clear-filters");
  if (clearButton) clearButton.addEventListener("click", clearAllFilters);
}

function clearAllFilters() {
  unpin();
  stackSimilar = false;
  $("chip-stack").classList.remove("active");
  hideKnownSoftware = false;
  showContainers = false;
  $("chip-nsrl").classList.remove("active");
  $("chip-containers").classList.remove("active");
  similarOnly = null;
  filter = "all";
  catFilter = "";
  flagFilter = new Set();
  facetFilter = {};
  $("search").value = "";
  document.querySelectorAll(".chip[data-filter]").forEach(c =>
    c.classList.toggle("active", c.dataset.filter === "all"));
  page = 0;
  render();
  if (pane === "classify") renderClassification();
  if (pane === "filters") renderFilters();
}

// ---- review: categories, flags and notes -------------------------------
let categories = [], caseFlags = [], decisions = {}, noteTimer = null;
// When a running job's case was last asked for (see poll).
let lastCaseLoad = 0;
// The code meaning "nobody has decided yet". Comes from the case rather than
// being assumed, because it is not 0: 0 is Non-Pertinent, which is a finding.
let uncategorizedCode = 8;
// The regional scheme the case grades under ({id, label, ...}, from /api/case).
let caseRegion = null;
// "" means every category; otherwise the code being shown.
let catFilter = "";
let caseExaminer = "";
let caseCounts = {};
// Set by "find similar images": the exact files to show, cleared like any
// other filter.
let similarOnly = null;
// Guards against a redraw inside a redraw: the ribbon's own handlers call
// render(), and render() redraws the ribbon.
let renderingRibbon = false;

// Which ask is current, and whose case the page holds. An answer that
// arrives after a newer ask, or after Start has moved to another job, is
// for a case the page has left: applied, it put the last case's grades on
// this run's files, and its categories then stopped this run asking for
// its own (the poll asked only while there were no categories).
let caseGen = 0;
let caseFor = null;

async function loadCase() {
  if (!jobId) return;
  const forJob = jobId, gen = ++caseGen;
  try {
    const d = await api(`/api/case?job=${forJob}`);
    if (gen !== caseGen || forJob !== jobId) return;
    caseFor = forJob;
    categories = d.categories; caseFlags = d.flags; decisions = d.decisions;
    uncategorizedCode = d.uncategorized;
    caseRegion = d.region || null;
    // The categories blurred by default are this scheme's notable ones. The
    // US numbers [1,2,3,9] were used for every scheme: a Swedish case
    // blurred its Uncategorized (2) and left its most severe grade (6) clear.
    blurCategories = new Set(blurChoiceFor(caseRegion));
    renderBlurCategories();
    caseExaminer = d.examiner || caseExaminer;
    caseCounts = d.counts || caseCounts;
    if (pane === "classify") renderClassification();
    render();   // which redraws the category bar from the decisions
  } catch (e) { /* no case yet */ }
}

function countsFromDecisions() {
  // Counted from the page's own copy of the decisions rather than from a
  // number the server sent earlier. Grading no longer re-reads the whole
  // case after every keystroke -- that is what made it fast -- so a count
  // fetched at load time goes stale the moment anything is graded, and the
  // bar sat there showing yesterday's figures.
  const counts = {};
  for (const file of files) {
    const decided = decisions[file.sha256];
    const code = decided ? decided.category : uncategorizedCode;
    counts[code] = (counts[code] || 0) + 1;
  }
  return counts;
}

function renderCategoryFilter(counts) {
  counts = counts || (paged ? ((pv.counts || {}).by_category || {})
                            : countsFromDecisions());
  // A file with no row in the case is uncategorized, and so is one explicitly
  // set to that code; the count has to include both.
  const countOf = c => counts[c.code] || 0;

  $("cat-bar").innerHTML =
    `<span class="catchip ${catFilter === "" ? "on" : ""}" data-cat-filter="">` +
    `All <span class="n">${caseSize().toLocaleString()}</span></span>` +
    categories.map(c => {
      const n = countOf(c);
      return `<span class="catchip ${String(c.code) === catFilter ? "on" : ""}` +
        `${n ? "" : " empty"}" data-cat-filter="${c.code}" ` +
        `style="--cat:${esc(c.color)}" title="${esc(c.name)}` +
        `${c.key ? ` — press ${c.key} to apply it to the selected file` : ""}">` +
        `${c.key ? `<span class="key">${esc(c.key)}</span>` : ""}` +
        `${esc(c.name)} <span class="n">${n.toLocaleString()}</span></span>`;
    }).join("");

  $("cat-bar").querySelectorAll("[data-cat-filter]").forEach(chip =>
    chip.addEventListener("click", () => {
      // Clicking the one already showing goes back to everything, so the bar
      // is its own way out.
      const wanted = chip.dataset.catFilter;
      catFilter = (wanted === catFilter) ? "" : wanted;
      page = 0;
      render();
    }));

  const active = categories.find(c => String(c.code) === catFilter);
  $("cat-selected").textContent = active
    ? `showing ${active.name}` : "";
}

function wireReview(box, sha256) {
  box.querySelectorAll("[data-cat]").forEach(b =>
    b.addEventListener("click", () => categorize(sha256, parseInt(b.dataset.cat, 10))));
  box.querySelectorAll("[data-delflag]").forEach(x =>
    x.addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm("Delete this flag from the case? It is removed from every "
                   + "file carrying it.")) return;
      await api("/api/case/flag", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({job: jobId, delete: parseInt(x.dataset.delflag, 10)})});
      await loadCase();
      showDetails(selectedPath);
    }));
  box.querySelectorAll("[data-flag]").forEach(b =>
    b.addEventListener("click", () =>
      toggleFlag(sha256, parseInt(b.dataset.flag, 10))));
  const newFlag = box.querySelector("#flag-new");
  if (newFlag) newFlag.addEventListener("click", async () => {
    const name = prompt("Name for the new flag (Evidence, For review, …)");
    if (!name) return;
    await api("/api/case/flag", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, create: name})});
    await loadCase();
    showDetails(selectedPath);
  });

  const note = box.querySelector("#file-note");
  if (note) note.addEventListener("focus", () => {
    note.dataset.was = note.value;
  });
  if (note) note.addEventListener("change", () => {
    if (note.dataset.was !== undefined && note.dataset.was !== note.value)
      rememberAction({kind: "note", digests: [sha256], was: note.dataset.was,
                      label: "note"});
    note.dataset.was = note.value;
  });
  if (note) note.addEventListener("input", () => {
    // Autosave a second after typing stops: every keystroke would be a write
    // per character, and a Save button is a thing to forget.
    clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => {
      await api("/api/case/note", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({job: jobId, sha256: sha256, note: note.value})});
      $("save-state").textContent = "saved " +
        new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    }, 900);
  });
}

$("sel-all").addEventListener("click", async () => {
  // Everything the current filter and search match, not just this page:
  // "all 2,400 uncategorized" is the selection an examiner means.
  if (paged) {
    try {
      const d = await galleryQuery({ids: true, limit: 1});
      (d.ids || []).forEach(([path, digest]) => knownDigest.set(path, digest));
      setSelection((d.ids || []).map(([path]) => path));
    } catch (e) { $("err").textContent = e.message; }
    return;
  }
  setSelection(visible().map(f => f.rel_path));
});
$("sel-page").addEventListener("click", () =>
  setSelection(pageOf(visible()).map(f => f.rel_path)));
$("sel-clear").addEventListener("click", () => { setSelection([]); });

// Grading has to feel immediate. The decision is shown at once and sent in
// the background: waiting for the round trip before moving on is what made
// a page of fifteen files feel like work, and waiting for the *whole case*
// to be re-read afterwards is what made it slow.
let gradeSequence = 0;

function applyLocally(digests, code) {
  const by = caseExaminer || "";
  digests.forEach(digest => {
    const existing = decisions[digest] || {flags: [], note: ""};
    decisions[digest] = Object.assign({}, existing,
      {category: code, by: by, auto: false});
  });
}

// What Undo reverses, newest last (v2026.11.320, U5). Gradings are undone by
// the case database, which keeps them in batches; flags and notes are not
// batched there, so they are put back from here. Ctrl-Z used to skip a flag
// set a moment ago and undo the grading before it.
const actions = [];
function rememberAction(action) {
  actions.push(action);
  if (actions.length > 50) actions.shift();
}

function nameOf(digests) {
  if (digests.length !== 1) return `${digests.length.toLocaleString()} files`;
  const file = lookupDigest(digests[0]);
  return file ? file.name : digests[0].slice(0, 12) + "…";
}

async function undoLast() {
  if (!jobId) return;
  const last = actions[actions.length - 1];
  if (last && last.kind !== "grade") {
    actions.pop();
    try {
      if (last.kind === "flag") {
        await api("/api/case/flag", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({job: jobId, sha256: last.digests,
                                flag_id: last.flagId, on: !last.on})});
      } else {
        await api("/api/case/note", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({job: jobId, sha256: last.digests[0], note: last.was})});
      }
      await loadCase();
      render();
      if (selectedPath) showDetails(selectedPath);
      const said = last.kind === "flag"
        ? `Undone: ${esc(last.label)} ${last.on ? "taken off" : "put back on"} ` +
          `${esc(nameOf(last.digests))}.`
        : `Undone: the note on ${esc(nameOf(last.digests))} is back as it was.`;
      $("status").textContent = said.replace(/&[a-z#0-9]+;/g, " ");
      toast(said);
    } catch (e) { $("err").textContent = e.message; }
    return;
  }
  try {
    const pending = await api(`/api/case/undo?job=${jobId}`);
    const next = (pending.batches || [])[0];
    if (!next) { $("status").textContent = "There is nothing to undo."; return; }
    if (next.files > 50 && !confirm(
        `Undo "${next.detail}" — ${next.files.toLocaleString()} file(s), ` +
        `graded at ${next.at.replace("T", " ")}?`)) return;

    const d = await api("/api/case/undo", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    if (!d.undone && d.reason) { toast(esc(d.reason), true); return; }
    if (last && last.kind === "grade") actions.pop();
    await loadCase();     // the decisions have changed wholesale
    render();
    // Which file, and what it is now, rather than a batch number.
    const digests = d.files || (last ? last.digests : []);
    const now = digests.length === 1 ? gradeText(digests[0]) : "";
    const said = digests.length
      ? `Undone: ${nameOf(digests)}` + (now ? ` is ${now} again` : " put back") +
        (last && last.label ? ` (was ${last.label})` : "") + "."
      : `Undone: ${d.undone.toLocaleString()} file(s) put back as they were ` +
        `(${d.detail}).`;
    $("status").textContent = said;
    toast(esc(said));
    paintViewerGrade();
  } catch (e) { $("err").textContent = e.message; }
}

// A stacked tile stands for its whole group, so grading it grades every
// file in that group. Leaving the others ungraded behind a tile that looks
// dealt with is the worst of both. Null when the group could not be read.
async function widenToStacks(digests) {
  if (!stackSimilar) return digests;
  if (paged) {
    // The group's other files are on the server, not here.
    const groups = [...new Set(digests.map(d => (lookupDigest(d) || {}).similar_group)
                                       .filter(Boolean))];
    if (!groups.length) return digests;
    try {
      const d = await galleryQuery({groups, limit: 1});
      return [...new Set([...digests, ...(d.sha256 || [])])];
    } catch (e) { $("err").textContent = e.message; return null; }
  }
  const widened = new Set();
  digests.forEach(digest => {
    const file = files.find(f => f.sha256 === digest);
    stackMembers(file).forEach(member => widened.add(member));
  });
  return [...widened].filter(Boolean);
}

async function categorize(sha256, code) {
  const many = selection.size > 1
    ? (paged ? await digestsFor([...selection]) : selectedDigests()) : null;
  let digests = many || (sha256 ? [sha256] : []);
  if (!digests.length) return;

  digests = await widenToStacks(digests);
  if (digests === null) return;

  // Work out where to go *before* grading changes what is visible. Doing it
  // afterwards means looking for the current file in a list it has just
  // left: with a filter on Uncategorized the cursor either stalls or lands
  // a file further on, which is the "it skipped one" behavior.
  const goingTo = many ? nextAfterSelection() : nextAfter(sha256);

  // An action covering more than the page is one nobody can see the whole
  // of, and it is exactly how a whole case gets graded by accident. It asks
  // once, and says how many and as what. Anything within the page does not
  // ask: that is the normal work, and a dialog on every keystroke is its
  // own kind of harm.
  const onPage = pageOf(visible()).length;
  if (digests.length > Math.max(onPage, 1)) {
    const label = (categories.find(c => c.code === code) || {}).name || code;
    if (!confirm(
        `Categorize ${digests.length.toLocaleString()} files as "${label}"?\n\n` +
        `That is more than the ${onPage.toLocaleString()} on this page. ` +
        `It can be undone with Ctrl-Z or the Undo button.`)) {
      return;
    }
  }

  // What each file held before, so a failed save can restore it. Deleting
  // the entry instead dropped an earlier grade, its flags and its note from
  // the page -- still on disk -- under a message saying they were put back.
  const before = new Map(digests.map(digest => [digest, decisions[digest]]));
  const label = (categories.find(c => c.code === code) || {}).name || String(code);
  if ($("full-modal").classList.contains("open")) {
    const shown = lookupDigest(digests[0]);
    confirmGrade(digests.length > 1
      ? `${digests.length.toLocaleString()} files: ${label}`
      : `${shown ? shown.name : "file"}: ${label}`);
  }
  applyLocally(digests, code);
  const ticket = ++gradeSequence;
  // A paged case's next page and its counts are the server's, worked out
  // from case.db -- so whatever needs them waits for the grading to be
  // stored. Moving within the page does not, and stays immediate.
  let afterSave = null;
  if (paged) {
    paintGraded(digests);
    if (many) {
      afterSave = () => {
        setSelection([]);
        afterBulkGrade();
        if (goingTo && !goingTo.after) moveTo(goingTo);
      };
    } else if (goingTo && goingTo.after) {
      afterSave = () => moveTo(goingTo);
    } else {
      moveTo(goingTo);
    }
  } else if (many) {
    setSelection([]);
    afterBulkGrade();
    moveTo(goingTo);
  } else {
    // Repaint the one tile, then move the cursor. No page rebuild.
    paintGraded(digests);
    moveTo(goingTo);
  }

  try {
    const d = await api("/api/case/categorize", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: digests, category: code})});
    rememberAction({kind: "grade", digests, label, before});
    if (ticket !== gradeSequence) return;   // a newer grading has happened
    $("save-state").textContent =
      (many ? `${d.changed.toLocaleString()} categorized · ` : "") + "saved " +
      new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    caseCounts = d.counts || caseCounts;
    if (pane === "classify") renderClassification();
    if (afterSave) afterSave();
    refreshCounts();
  } catch (e) {
    // The decision was shown before it was stored, so a failure has to undo
    // it rather than leave the examiner believing a file is graded -- unless
    // a newer grading has been made since: put back blindly, a file graded 1
    // then 2, whose first save failed after the second succeeded, showed as
    // ungraded while disk held 2 (v2026.11.319). Then the case is read again.
    if (ticket === gradeSequence) {
      before.forEach((was, digest) => {
        if (was === undefined) delete decisions[digest];
        else decisions[digest] = was;
      });
      render();
    } else {
      await loadCase();
    }
    $("err").textContent =
      `That grading was not saved: ${e.message}. The files have been put back ` +
      `as they were.`;
  }
}

async function toggleFlag(sha256, flagId) {
  const many = selection.size > 1
    ? (paged ? await digestsFor([...selection]) : selectedDigests()) : null;
  const digests = many || [sha256];
  const current = decisions[sha256];
  const on = !(current && current.flags && current.flags.indexOf(flagId) >= 0);

  // Shown before it is stored, like a category.
  digests.forEach(digest => {
    const entry = decisions[digest] || {category: uncategorizedCode, flags: []};
    const flags = new Set(entry.flags || []);
    on ? flags.add(flagId) : flags.delete(flagId);
    decisions[digest] = Object.assign({}, entry, {flags: [...flags]});
  });
  paintGraded(digests);
  if (selectedPath && pane === "details") showDetails(selectedPath);

  try {
    await api("/api/case/flag", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, sha256: digests,
                            flag_id: flagId, on: on})});
    const flagName = (caseFlags.find(f => f.id === flagId) || {}).name || "flag";
    rememberAction({kind: "flag", digests, flagId, on, label: flagName});
    if ($("full-modal").classList.contains("open"))
      confirmGrade(`${flagName} ${on ? "on" : "off"}`);
    $("save-state").textContent = "saved " +
      new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    await loadCase();
    if (selectedPath) showDetails(selectedPath);
  } catch (e) { $("err").textContent = e.message; }
}

// ---- the hash store, which outlives the case ---------------------------
async function connectRememberedStore(kept) {
  const where = kept.store_path;
  try {
    const d = await api("/api/store", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: where})});
    renderStore(d);
    $("store-path").value = d.path || where;
    $("store-state").textContent =
      `connected · ${(d.total || 0).toLocaleString()} hash(es)`;
  } catch (e) {
    // Said in the tab rather than as an error: the store being unreachable
    // at startup is a thing to know, not a failure of the program, and the
    // examiner may not need the store for what they are about to do.
    $("store-state").textContent = "not connected";
    $("store-err").innerHTML =
      `<span class="warn">The store this machine used last time could not ` +
      `be opened: ${esc(e.message)}</span>`;
  }
}

function renderStore(d) {
  // A store on a share is the one mistake worth interrupting for: it works
  // until the day it does not, and the day it does not it can take the
  // store with it.
  if (d && d.warning) {
    $("store-err").innerHTML = `<span class="warn">${esc(d.warning)}</span>`;
  }
  $("store-path").value = d.path || "";
  // A unit contributes a few hundred cases a year, and every one of them
  // is a source. Listing them all turns the one place an examiner looks
  // at the reference sets into a page of case numbers they have to scroll
  // past -- so the sets are listed and the cases are counted. The server's
  // own admin pages still list every one, which is where that belongs.
  const imported = d.sources.filter(src => src.kind !== "case");
  const fromCases = d.sources.filter(src => src.kind === "case");
  const caseHashes = fromCases.reduce((sum, src) => sum + (src.hashes || 0), 0);

  $("store-list").innerHTML = d.sources.length
    ? `<p class="hint">${count(d.total)} hash(es) across ` +
      `${count(d.sources.length)} source(s).</p>` +
      imported.map(src => `<div class="sig-row">
        <span>${esc(src.name)} — <strong>${count(src.hashes)}</strong>
          hashes (${esc(src.kind)}) · added ${esc((src.added_at || "").slice(0, 10))}
          ${src.note ? `<br><span class="hint">${esc(src.note)}</span>` : ""}</span>
        <button class="secondary" data-store-remove="${esc(src.name)}">Remove</button>
      </div>`).join("") +
      (fromCases.length
        ? `<div class="sig-row">
            <span><strong>${count(caseHashes)}</strong> hash(es) contributed
              by <strong>${count(fromCases.length)}</strong> case(s)
              <br><span class="hint">Cases are not listed here. The
              server's admin pages list every one, with who contributed it
              and when; removing one is done there.</span></span>
            <button class="secondary" id="store-show-cases">Show them</button>
          </div>`
        : "")
    : '<p class="hint">Nothing in the store yet.</p>';

  const showCases = $("store-show-cases");
  if (showCases) {
    showCases.addEventListener("click", () => {
      // Shown on request rather than never: an examiner looking for one
      // case should be able to find it without leaving the program.
      const rows = fromCases
        .slice()
        .sort((a, b) => (b.added_at || "").localeCompare(a.added_at || ""))
        .map(src => `<div class="sig-row">
          <span>${esc(src.name)} — <strong>${count(src.hashes)}</strong>
            hashes · added ${esc((src.added_at || "").slice(0, 10))}</span>
          <button class="secondary" data-store-remove="${esc(src.name)}">Remove</button>
        </div>`).join("");
      showCases.closest(".sig-row").insertAdjacentHTML("afterend", rows);
      showCases.remove();
      $("store-list").querySelectorAll("[data-store-remove]").forEach(b =>
        b.addEventListener("click", () => removeStoreSource(b)));
    });
  }
  $("store-list").querySelectorAll("[data-store-remove]").forEach(b =>
    b.addEventListener("click", async () => {
      if (!confirm(`Remove "${b.dataset.storeRemove}" and all its hashes from ` +
                   `the store? The original file is not touched.`)) return;
      try { renderStore(await api("/api/store/remove", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: b.dataset.storeRemove})}));
      } catch (e) { $("store-err").textContent = e.message; }
    }));
}

$("store-forget").addEventListener("click", async () => {
  if (!confirm("Forget the hash store settings on this machine?\n\n" +
               "The address, the certificate path and the token, if it was " +
               "remembered. The store itself is untouched.")) return;
  try {
    await api("/api/store/forget", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({all: true})});
    $("store-token").value = "";
    $("store-remember").checked = false;
    $("status").textContent = "This machine has forgotten the store settings.";
  } catch (e) { $("store-err").textContent = e.message; }
});

$("store-cert-browse").addEventListener("click", () =>
  openBrowser(p => { $("store-cert").value = p; }, $("store-cert").value.trim(),
              "Choose the server certificate (server.crt)"));

$("store-browse").addEventListener("click", () =>
  openBrowser(p => { $("store-path").value = p; }, $("store-path").value.trim(),
              "Choose a hash store file"));
$("store-import-browse").addEventListener("click", () =>
  openBrowser(p => { $("store-import").value = p; }, $("store-import").value.trim(),
              "Choose a hash set to import"));

$("store-open").addEventListener("click", async () => {
  const token = ($("store-token") && $("store-token").value || "").trim();
  const cert = ($("store-cert") && $("store-cert").value || "").trim();
  const where = ($("store-path").value || "").trim();
  // A server address and no token is the mistake worth catching here
  // rather than in a refusal from the far end.
  if (/^https?:\/\//i.test(where) && !token) {
    $("store-err").textContent =
      "That is a server address, so it needs the token you were issued.";
    return;
  }
  const remember = $("store-remember") && $("store-remember").checked;
  if (token || cert || remember) {
    try {
      await api("/api/store/token", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token, cert, remember})});
    } catch (e) { $("store-err").textContent = e.message; return; }
  }
  $("store-err").textContent = "";
  try {
    renderStore(await api("/api/store", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: $("store-path").value.trim()})}));
  } catch (e) { $("store-err").textContent = e.message; }
});

$("store-verify").addEventListener("click", async () => {
  $("store-err").textContent = "";
  try {
    const d = await api("/api/store/verify");
    const r = d.report || {};
    // A file and a server answer different questions, and neither answer
    // should be assumed complete: every figure below is read defensively,
    // because one missing field used to throw and leave the examiner with
    // a message about toLocaleString.
    const sources = Array.isArray(r.sources) ? r.sources : [];
    let text;
    if (r.error) {
      text = `<span class="warn">${esc(r.error)}</span>`;
    } else if (r.remote) {
      text = `Asked the server at <span class="p">${esc(r.path || "")}</span>: ` +
             `<strong>${count(r.total)}</strong> hash(es) across ` +
             `${sources.length} source(s).<br>` +
             `Signed in as <strong>${esc(r.who || "")}</strong>` +
             (r.role ? ` (${esc(r.role)})` : "") +
             (Array.isArray(r.may) && r.may.length
               ? `, which may ${esc(r.may.join(", "))}` : "") + `.` +
             (r.server_version
               ? `<br>server ${esc(r.server_version)}` +
                 (r.server_current ? "" : " — out of date")
               : "") +
             (r.server_note
               ? `<br><span class="warn">${esc(r.server_note)}</span>`
               : `<br><span class="hint">The address, the token and the ` +
                 `certificate all work.</span>`);
    } else if (r.exists === false) {
      text = `<span class="warn">There is no store file at ` +
             `${esc(r.path || "that path")} yet. Importing a set creates ` +
             `one.</span>`;
    } else {
      const algorithms = Object.entries(r.by_algorithm || {})
        .map(([a, n]) => `${count(n)} ${a}`).join(", ");
      text = `Read back from the file itself: <strong>${count(r.total)}</strong> ` +
             `hash(es)` + (algorithms ? ` (${esc(algorithms)})` : "") +
             ` across ${sources.length} source(s)` +
             (typeof r.size === "number"
               ? `, ${(r.size / 1048576).toFixed(1)} MB.` : ".") +
             (r.side_files && r.side_files["-wal"] > 4096
               ? `<br><span class="warn">A ` +
                 `${(r.side_files["-wal"] / 1048576).toFixed(1)} MB ` +
                 `write-ahead log sits beside it; copy that file too, or the ` +
                 `copy will be missing recent imports.</span>` : "");
    }
    $("store-result").innerHTML = text;
    $("store-result").style.display = "block";
  } catch (e) { $("store-err").textContent = e.message; }
});

$("store-inspect").addEventListener("click", async () => {
  $("store-err").textContent = "";
  const path = $("store-import").value.trim();
  if (!path) { $("store-err").textContent = "Choose a file first."; return; }
  try {
    const d = await api("/api/store/inspect", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})});
    const r = d.report;
    $("store-result").innerHTML =
      `<strong>${esc(r.path.split(/[\\/]/).pop())}</strong> — ` +
      `${(r.size / 1048576).toFixed(1)} MB, looks like <strong>${esc(r.kind)}</strong>` +
      `${r.byte_order_mark ? " (with a byte order mark)" : ""}.<br>` +
      `Read structurally: <strong>${r.structured_sample.length}</strong> sample hash(es). ` +
      `Found by scanning: <strong>${r.scanned_sample.length}</strong>.` +
      (r.reader_error ? `<br><span class="warn">reader failed: ` +
                        `${esc(r.reader_error)}</span>` : "") +
      (r.has_media_array === false
        ? `<br><span class="warn">No "Media" array near the start, which is ` +
          `where a VICS file keeps its records. Keys seen: ` +
          `${esc((r.top_level_keys || []).join(", "))}</span>` : "") +
      `<div class="p" style="margin-top:6px;">${esc(r.starts_with)}</div>`;
    $("store-result").style.display = "block";
  } catch (e) { $("store-err").textContent = e.message; }
});

$("store-import-go").addEventListener("click", async () => {
  $("store-err").textContent = "";
  const path = $("store-import").value.trim();
  if (!path) { $("store-err").textContent = "Choose a hash set file first."; return; }
  $("store-import-go").disabled = true;
  $("store-import-go").textContent = "Importing…";
  try {
    const d = await api("/api/store/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})});
    renderStore(d);
    $("store-import").value = "";
    const imp = d.imported;
    const skipped = Object.entries(imp.skipped || {})
      .map(([why, n]) => `${n.toLocaleString()} skipped (${esc(why)})`).join(", ");
    $("store-result").innerHTML =
      (imp.stored
        ? `Imported <strong>${imp.stored.toLocaleString()}</strong> hash(es) ` +
          `as ${esc(imp.source)} in ${imp.seconds}s, read as ${esc(imp.kind)}.`
        : `<span class="warn">No hashes were added from ` +
          `${esc(imp.source)}.</span>`) +
      (imp.read ? ` ${imp.read.toLocaleString()} row(s) read.` : "") +
      (skipped ? `<br>${skipped}` : "") +
      (imp.note ? `<br><span class="warn">${esc(imp.note)}</span>` : "");
    $("store-result").style.display = "block";
  } catch (e) { $("store-err").textContent = e.message; }
  finally { $("store-import-go").disabled = false;
            $("store-import-go").textContent = "Import"; }
});

// Press the store check by itself after a run, if there is a store to ask.
async function autoStoreCheck() {
  try {
    const where = await api("/api/store");
    if (where.none_yet || !where.total) return;      // no store set up
  } catch (e) { return; }                            // unreachable: say nothing
  if (!$("store-scan").disabled) $("store-scan").click();
  toast("Checking this run against the hash store; categories it sets are "
        + "marked as set by a hash set.");
}

$("store-scan").addEventListener("click", async () => {
  $("store-err").textContent = "";
  if (!jobId) { $("store-err").textContent = "Run an extraction first."; return; }
  $("store-scan").disabled = true;
  $("store-scan").textContent = "Checking…";
  try {
    const d = await api("/api/store/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    $("store-result").innerHTML =
      `Checked ${d.checked.toLocaleString()} file(s)` +
      (d.locations && d.locations !== d.checked
        ? ` (found in ${d.locations.toLocaleString()} places)` : "") + `: ` +
      `<strong>${d.matched.toLocaleString()}</strong> in the store's sets` +
      (d.your_stash ? `, <strong>${d.your_stash.toLocaleString()}</strong> in your stash` : "") +
      `; ${d.unmatched.toLocaleString()} in neither — unknown, which is not the ` +
      `same as cleared.<br>` +
      (d.applied && d.applied.categorized
        ? `Of those, <strong>${d.applied.categorized.toLocaleString()}</strong> ` +
          `file(s) nobody had graded were categorized from the match — shown ` +
          `with a striped marker, and listed under "Set by a hash set".<br>` : "") +
      Object.entries(d.by_source).map(([n, c]) =>
        `${c.toLocaleString()} file(s) in ${esc(n)}`).join("<br>") +
      `<div class="p" style="margin-top:6px;">${esc(d.csv)}</div>`;
    $("store-result").style.display = "block";
    const st = await api(`/api/status?job_id=${jobId}`);
    files = takeFiles(st, files);
    // The check writes its categories to the case, and the gallery draws
    // them from the case's decisions -- which were never read again, so
    // every file it classified still showed as uncategorized until the case
    // was opened afresh (found in DC25-320: "it did not appear to classify").
    await loadCase();
    render();
    if (selectedPath) showDetails(selectedPath);
  } catch (e) { $("store-err").textContent = e.message; }
  finally { $("store-scan").disabled = false;
            $("store-scan").textContent = "Check this run against the store"; }
});

$("store-contribute").addEventListener("click", async () => {
  $("store-err").textContent = "";
  if (!jobId) { $("store-err").textContent = "Run an extraction first."; return; }
  const name = prompt("Name what is being added — usually the case number. A " +
                      "later match will say the decision came from here.",
                      $("case-number").value.trim());
  if (!name) return;
  try {
    const d = await api("/api/store/contribute", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, source: name})});
    renderStore(d);
    // A local store answers {hashes, skipped_uncategorized}; a shared store
    // answered {stored} before it was brought into line, and a server not
    // yet updated still may. Read either, so the line is never a TypeError
    // about undefined in place of what was added.
    const added = d.added || {};
    const hashes = added.hashes ?? added.stored;
    $("store-result").innerHTML =
      `Added <strong>${count(hashes)}</strong> hash(es)` +
      (typeof added.files === "number"
        ? ` from ${count(added.files)} graded file(s)` : "") +
      (added.source ? ` as ${esc(added.source)}` : "") + `. ` +
      (typeof added.skipped_uncategorized === "number"
        ? `${count(added.skipped_uncategorized)} uncategorized file(s) ` +
          `were not added — an ungraded hash teaches the next case nothing.`
        : "") +
      (typeof added.regraded === "number" && added.regraded
        ? ` ${count(added.regraded)} earlier grade(s) in this case were ` +
          `changed; the store records each change.`
        : "");
    $("store-result").style.display = "block";
  } catch (e) { $("store-err").textContent = e.message; }
});

api("/api/store").then(renderStore).catch(() => {});

// ---- hash sets ----------------------------------------------------------
$("hash-browse").addEventListener("click", () => {
  openBrowser(p => { $("hash-path").value = p; }, $("hash-path").value.trim(),
              "Choose a hash set");
});

async function renderHashSets(sets) {
  $("hash-list").innerHTML = sets.length
    ? sets.map(s => `<div class="sig-row">
        <span>${esc(s.name)} — <strong>${s.size.toLocaleString()}</strong> hashes
          (${esc(s.kind)})${s.meaning ? `<br><span class="hint">${esc(s.meaning)}</span>` : ""}</span>
        <button class="secondary" data-remove="${esc(s.path)}">Remove</button></div>`).join("")
    : '<p class="hint">No sets loaded.</p>';
  $("hash-list").querySelectorAll("[data-remove]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        const d = await api("/api/hashsets/remove", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: b.dataset.remove})});
        renderHashSets(d.sets);
      } catch (e) { $("hash-err").textContent = e.message; }
    }));
}

$("hash-add").addEventListener("click", async () => {
  $("hash-err").textContent = "";
  const path = $("hash-path").value.trim();
  if (!path) { $("hash-err").textContent = "Choose a hash set file first."; return; }
  $("hash-add").disabled = true;
  $("hash-add").textContent = "Loading…";
  try {
    const d = await api("/api/hashsets", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})});
    renderHashSets(d.sets);
    $("hash-path").value = "";
  } catch (e) { $("hash-err").textContent = e.message; }
  finally { $("hash-add").disabled = false; $("hash-add").textContent = "Load"; }
});

$("hash-scan").addEventListener("click", async () => {
  $("hash-err").textContent = "";
  if (!jobId) { $("hash-err").textContent = "Run an extraction first."; return; }
  $("hash-scan").disabled = true;
  $("hash-scan").textContent = "Checking…";
  try {
    const d = await api("/api/hashsets/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    const lines = Object.entries(d.by_set)
      .map(([name, n]) => `${n.toLocaleString()} in ${esc(name)}`).join("<br>");
    $("hash-result").innerHTML =
      `Checked ${d.checked.toLocaleString()} file(s): ` +
      `<strong>${d.matched.toLocaleString()}</strong> matched, ` +
      `${d.unmatched.toLocaleString()} not in any loaded set — unknown, which ` +
      `is not the same as cleared.<br>${lines}` +
      `<div class="p" style="margin-top:6px;">${esc(d.csv)}</div>`;
    $("hash-result").style.display = "block";
    const s = await api(`/api/status?job_id=${jobId}`);
    files = takeFiles(s, files);
    await loadCase();      // its categories are in the case, not in files
    render();
    if (selectedPath) showDetails(selectedPath);
  } catch (e) { $("hash-err").textContent = e.message; }
  finally { $("hash-scan").disabled = false;
            $("hash-scan").textContent = "Check this run against the loaded sets"; }
});

// visual similarity is optional: show the controls only if it can run
(async () => {
  try {
    const c = caps || await api("/api/capabilities");
    if (c.similarity && c.similarity.available) {
      $("sim-controls").style.display = "flex";
    } else {
      $("sim-unavailable").style.display = "block";
      $("sim-unavailable").textContent =
        (c.similarity && c.similarity.message) ||
        "Visual similarity needs the Pillow imaging library.";
    }
  } catch (e) { /* capabilities not loaded yet */ }
})();

$("frames-match").addEventListener("click", async () => {
  $("sim-err").textContent = "";
  if (!jobId) { $("sim-err").textContent = "Run an extraction first."; return; }
  $("frames-match").disabled = true;
  $("frames-match").textContent = "Matching…";
  try {
    const d = await api("/api/frames/match", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    $("sim-result").innerHTML =
      `Hashed the key frames of <strong>${d.hashed.toLocaleString()}</strong> ` +
      `of ${d.videos.toLocaleString()} video(s)` +
      (d.unreadable ? `; ${d.unreadable.toLocaleString()} could not be read` : "") +
      `.<br><strong>${d.stills_matched.toLocaleString()}</strong> still(s) ` +
      `match a frame of one.<br>` +
      `<span class="hint">A match is the same picture to the eye, not the ` +
      `same file. A video is represented by a dozen frames out of thousands, ` +
      `so a still from a moment between them will not match: a miss here ` +
      `means less than a hit.</span>`;
    $("sim-result").style.display = "block";
    if (selectedPath && pane === "details") showDetails(selectedPath);
  } catch (e) { $("sim-err").textContent = e.message; }
  finally {
    $("frames-match").disabled = false;
    $("frames-match").textContent = "Match stills to videos";
  }
});

$("screen-scan").addEventListener("click", async () => {
  $("sim-err").textContent = "";
  if (!jobId) { $("sim-err").textContent = "Run an extraction first."; return; }
  $("screen-scan").disabled = true;
  $("screen-scan").textContent = "Scoring…";
  try {
    const d = await api("/api/screening/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId})});
    const bands = Object.entries(d.bands || {})
      .map(([k, n]) => `${esc(k)}: ${n.toLocaleString()}`).join(" · ");
    $("sim-result").innerHTML =
      `Scored <strong>${d.scored.toLocaleString()}</strong> of ` +
      `${d.images.toLocaleString()} image(s)` +
      (d.unreadable ? `; ${d.unreadable.toLocaleString()} could not be read` : "") +
      `.<br>${bands}<br>` +
      `<span class="hint">Filter on it under <em>Skin-tone proportion</em>. ` +
      `It says how much of a picture is in a skin-tone color band, nothing ` +
      `more: a low score is not "cleared".</span>`;
    $("sim-result").style.display = "block";
    const st = await api(`/api/status?job_id=${jobId}`);
    files = takeFiles(st, []);
    render();
    if (pane === "filters") renderFilters();
  } catch (e) { $("sim-err").textContent = e.message; }
  finally {
    $("screen-scan").disabled = false;
    $("screen-scan").textContent = "Score the images in this run";
  }
});

$("sim-scan").addEventListener("click", async () => {
  $("sim-err").textContent = "";
  if (!jobId) { $("sim-err").textContent = "Run an extraction first."; return; }
  $("sim-scan").disabled = true;
  $("sim-scan").textContent = "Hashing images…";
  try {
    const d = await api("/api/similarity/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, distance: parseInt($("sim-distance").value, 10)})});
    $("sim-result").innerHTML =
      `Hashed <strong>${d.hashed.toLocaleString()}</strong> of ` +
      `${d.images.toLocaleString()} image(s)` +
      (d.undecodable ? ` — ${d.undecodable.toLocaleString()} could not be ` +
        `decoded (truncated carves, unsupported formats, files that are not ` +
        `really images)` : "") +
      `.<br><strong>${d.stacks.toLocaleString()}</strong> visual stack(s) ` +
      `covering ${d.stacked_files.toLocaleString()} file(s) — the same ` +
      `picture, both hashes agreeing.<br>` +
      `<strong>${d.groups.toLocaleString()}</strong> looser group(s) covering ` +
      `${d.grouped_files.toLocaleString()} file(s) at ${d.max_distance} bits ` +
      `of difference.` +
      (d.featureless ? `<br>${d.featureless.toLocaleString()} featureless ` +
       `image(s) left out of grouping.` : "") +
      // Carved files are routinely truncated; the decoder's complaints are
      // counted rather than printed, so they can be seen without burying
      // everything else.
      (d.decode_notes && Object.keys(d.decode_notes).length
        ? `<br><span class="hint">While decoding: ` +
          esc(Object.entries(d.decode_notes)
               .map(([k, n]) => `${k} (${n.toLocaleString()})`).join("; ")) +
          `. Normal for carved files, which often stop mid-image.</span>` : "") +
      `<div class="p" style="margin-top:6px;">${esc(d.csv)}</div>`;
    $("sim-result").style.display = "block";
    const s2 = await api(`/api/status?job_id=${jobId}`);
    files = takeFiles(s2, files);
    render();
    if (selectedPath) showDetails(selectedPath);
  } catch (e) { $("sim-err").textContent = e.message; }
  finally { $("sim-scan").disabled = false;
            $("sim-scan").textContent = "Find similar images"; }
});

renderHashSets([]);
api("/api/hashsets").then(d => renderHashSets(d.sets)).catch(() => {});

// ---- Project VIC --------------------------------------------------------
$("vic-toggle").addEventListener("click", () => {
  const open = $("vic-panel").style.display !== "none";
  $("vic-panel").style.display = open ? "none" : "block";
  $("vic-toggle").textContent = open ? "show" : "hide";
});

$("vic-browse").addEventListener("click", () => {
  openBrowser(p => { $("vic-path").value = p; }, $("vic-path").value.trim());
});

async function vicCall(body, button) {
  $("vic-err").textContent = "";
  const path = $("vic-path").value.trim();
  if (!path) { $("vic-err").textContent = "Choose a VICS JSON file first."; return null; }
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Working…";
  try {
    return await api("/api/vic/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.assign({path}, body))
    });
  } catch (e) {
    $("vic-err").textContent = e.message;
    return null;
  } finally { button.disabled = false; button.textContent = label; }
}

$("vic-match").addEventListener("click", async () => {
  const d = await vicCall({job: jobId}, $("vic-match"));
  if (!d) return;
  const r = d.result;
  $("vic-result").innerHTML =
    `Set holds <strong>${count(r.hashes_in_set)}</strong> hash(es). ` +
    `Of ${count(r.files_checked)} recovered file(s): ` +
    `<strong>${count(r.matched)}</strong> in the set, ` +
    `${count(r.unmatched)} not in it — unknown, which is not the same as ` +
    `cleared.` +
    `<div class="p" style="margin-top:6px;">${esc(r.csv)}</div>`;
  $("vic-result").style.display = "block";
});

$("vic-import").addEventListener("click", async () => {
  const d = await vicCall({}, $("vic-import"));
  if (!d) return;
  claimPage();
  leaveReview();
  jobId = d.job_id;
  $("vic-result").innerHTML = "Importing the referenced media…";
  $("vic-result").style.display = "block";
  poll();
});

// ---- C4All / Griffeye export -------------------------------------------
$("export-toggle").addEventListener("click", () => {
  const open = $("export-panel").style.display !== "none";
  $("export-panel").style.display = open ? "none" : "block";
  $("export-toggle").textContent = open ? "show" : "hide";
});

$("reprocess-toggle").addEventListener("click", () => {
  const open = $("reprocess-panel").style.display !== "none";
  $("reprocess-panel").style.display = open ? "none" : "block";
  $("reprocess-toggle").textContent = open ? "show" : "hide";
});

// /api/case/reprocess was written, tested and reachable from nothing an
// examiner could press. These are its buttons.
for (const id of ["reprocess-expand", "reprocess-thumbs"]) {
  $(id).addEventListener("click", async () => {
    $("reprocess-err").textContent = "";
    if (!jobId) { $("reprocess-err").textContent = "Open a case first."; return; }
    const button = $(id), label = button.textContent;
    const reason = button.dataset.reason;
    for (const other of ["reprocess-expand", "reprocess-thumbs"]) $(other).disabled = true;
    button.textContent = "Working…";
    try {
      const d = await api("/api/case/reprocess", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({job: jobId, reason})});
      let html = esc(d.note || "Done.");
      if (reason === "expand-containers") {
        html = `Looked inside <strong>${count(d.looked_inside || 0)}</strong> ` +
               `file(s); recovered <strong>${count(d.recovered || 0)}</strong> ` +
               `more.` + (d.errors ? ` <span class="warn">${count(d.errors)} ` +
               `could not be opened.</span>` : "") + `<br>${html}`;
        // What this recovered is in the case now; show it.
        if (d.recovered) {
          const st = await api(`/api/status?job_id=${jobId}`);
          files = takeFiles(st, files);
          showSummary(st.summary, "done");
          render();
        }
      }
      $("reprocess-result").innerHTML = html;
      $("reprocess-result").style.display = "block";
    } catch (e) {
      $("reprocess-err").textContent = e.message;
    } finally {
      for (const other of ["reprocess-expand", "reprocess-thumbs"]) $(other).disabled = false;
      button.textContent = label;
    }
  });
}

$("btn-export").addEventListener("click", async () => {
  $("export-err").textContent = "";
  const caseNo = $("exp-case").value.trim();
  if (!caseNo) { $("export-err").textContent = "A case number is required."; return; }
  $("btn-export").disabled = true;
  $("btn-export").textContent = "Writing…";
  try {
    const d = await api("/api/export", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        job: jobId, case: caseNo,
        analyst: $("exp-analyst").value.trim(),
        evidence: $("exp-evidence").value.trim(),
        comment: $("exp-comment").value.trim(),
        media: $("exp-media").value,
        organization: $("exp-org").value.trim(),
        project_vic: $("exp-vic").checked,
        vic_files_layout: $("exp-vic-hash").checked ? "hash" : "inplace",
        copy: $("exp-copy").checked,
        all_as_movies: $("exp-all-movies").checked
      })
    });
    showExport(d.export);
    if ($("exp-lava").checked) {
      const l = await api("/api/export/lava", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          job: jobId, case: caseNo,
          examiner: $("exp-analyst").value.trim(),
          evidence: $("exp-evidence").value.trim(),
          absolute_media: $("exp-lava-abs").checked})
      });
      $("export-result").innerHTML +=
        `<br>LAVA: ${l.lava.artifacts} artifact(s), ${l.lava.rows} row(s), ` +
        `${l.lava.media_staged} media file(s) — ` +
        (l.valid ? "validated" : `<span class="warn">${esc(l.problems.join("; "))}</span>`) +
        `<div class="p">${esc(l.lava.output_dir)}/_lava_data.lava</div>` +
        (caps && caps.lava_installed
          ? `<button class="secondary" id="btn-open-lava" style="margin-top:6px;">Open in LAVA</button>`
          : `<span class="hint">LAVA not detected on this machine — open the ` +
            `.lava file by hand, or get it from ` +
            `<a href="${caps ? caps.lava_website : "https://www.leapps.org/#lava"}" ` +
            `target="_blank">leapps.org</a>.</span>`);
      const openBtn = $("btn-open-lava");
      if (openBtn) openBtn.addEventListener("click", async () => {
        openBtn.disabled = true;
        try {
          await api("/api/export/lava/open", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({job: jobId})});
          openBtn.textContent = "Opened in LAVA";
        } catch (e) {
          $("export-err").textContent = e.message;
          openBtn.disabled = false;
        }
      });
    }
  } catch (e) {
    $("export-err").textContent = e.message;
  } finally {
    $("btn-export").disabled = false;
    $("btn-export").textContent = "Write Report Set";
  }
});

function showExport(r) {
  const labels = {
    "ProjectVIC.json": "ProjectVIC.json — load this one in Griffeye",
    "C4M_Index.xml": "C4M_Index.xml (movies)",
    "C4P_Index.xml": "C4P_Index.xml (pictures)",
  };
  const links = r.files_written.map(n =>
    `<a href="/api/export/file?job=${jobId}&name=${encodeURIComponent(n)}">` +
    `${esc(labels[n] || n)}</a>`
  ).join(" · ");
  $("export-result").innerHTML =
    `<strong>${r.total} file(s) exported</strong> — ${r.movies} in the movie index, ` +
    `${r.pictures} in the picture index, across ${r.discs.length} disc folder(s) ` +
    `(${esc(r.placement.join(", ") || "none")}).` +
    (r.project_vic && !r.project_vic.error
      ? `<br>ProjectVIC.json: ${r.project_vic.total} media record(s), Category null on all of them — grade them in Griffeye.`
      : r.project_vic && r.project_vic.error
        ? `<span class="warn">⚠ Project VIC export failed: ${esc(r.project_vic.error)}</span>` : "") +
    (r.missing.length ? `<span class="warn">⚠ ${r.missing.length} file(s) could not be placed</span>` : "") +
    (r.stale_removed ? `<br>${r.stale_removed} file(s) from an earlier export of this folder were removed.` : "") +
    ((r.stale_kept || []).length
      ? `<br><span class="warn">⚠ ${r.stale_kept.length} file(s) from an earlier ` +
        `export are still in the disc folders, because removing them could ` +
        `lose the only copy (they were moved there). They are not in the new ` +
        `index; move them out before burning or loading this folder.</span>` : "") +
    `<div class="p" style="margin-top:6px;">${esc(r.output_dir)}</div>` +
    `<div style="margin-top:6px;">${links}</div>`;
  $("export-result").style.display = "block";
}

// ---- file picker --------------------------------------------------------
let picked = null, pickTarget = null;

function openBrowser(onPick, startPath, title) {
  pickTarget = onPick;
  $("browse-title").textContent = title || "Choose an image file";
  $("browse-modal").classList.add("open");
  loadDir(startPath ? dirOf(startPath) : "");
}

$("btn-browse").addEventListener("click", () => {
  openBrowser(p => { $("path").value = p; }, $("path").value.trim());
});
$("browse-close").addEventListener("click", () => $("browse-modal").classList.remove("open"));
$("browse-modal").addEventListener("click", e => {
  if (e.target === $("browse-modal")) $("browse-modal").classList.remove("open");
});
function dirOf(p) { const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return i > 0 ? p.slice(0, i) : ""; }

async function loadDir(path) {
  try {
    const d = await api("/api/browse?path=" + encodeURIComponent(path || ""));
    $("browse-path").value = d.path || "Drives";
    // "" means the drive list, null means there is genuinely nothing above.
    // Treating them the same is what trapped the picker on one drive.
    $("browse-up").dataset.parent = d.parent === null ? "" : d.parent;
    $("browse-up").disabled = d.parent === null || d.at_drive_list;

    $("browse-roots").innerHTML = (d.roots || []).map((r, i) =>
      `<span class="chip" data-root="${i}">${esc(r.name)}</span>`).join("");
    $("browse-roots").querySelectorAll("[data-root]").forEach(el =>
      el.addEventListener("click", () =>
        loadDir(d.roots[parseInt(el.dataset.root, 10)].path)));
    picked = null; $("browse-pick").disabled = true;
    $("browse-sel").textContent = "No file selected.";
    $("browse-list").innerHTML = d.entries.map((e, i) =>
      `<div class="entry" data-i="${i}" data-dir="${e.is_dir}" data-path="${esc(e.path)}">
         <span>${e.is_dir ? "📁 " : (e.likely_image ? "💽 " : "📄 ")}${esc(e.name)}</span>
         <span style="color:var(--muted)">${esc(e.size_human || "")}</span>
       </div>`).join("") || '<p class="hint" style="padding:10px;">Empty.</p>';
    $("browse-list").querySelectorAll(".entry").forEach(el =>
      el.addEventListener("click", () => {
        if (el.dataset.dir === "true") { loadDir(el.dataset.path); return; }
        $("browse-list").querySelectorAll(".entry").forEach(x => x.classList.remove("sel"));
        el.classList.add("sel");
        picked = el.dataset.path;
        $("browse-sel").textContent = picked;
        $("browse-pick").disabled = false;
      }));
  } catch (e) { $("browse-list").innerHTML = `<p class="hint" style="padding:10px;color:var(--err)">${esc(e.message)}</p>`; }
}
$("browse-up").addEventListener("click", () => loadDir($("browse-up").dataset.parent));
$("browse-pick-dir").addEventListener("click", () => {
  const dir = $("browse-path").value;
  if (!dir || dir === "Drives") return;
  if (pickTarget) pickTarget(dir); else $("path").value = dir;
  $("browse-modal").classList.remove("open");
});

$("browse-pick").addEventListener("click", () => {
  if (!picked) return;
  if (pickTarget) pickTarget(picked);
  else $("path").value = picked;
  $("browse-modal").classList.remove("open");
});

// Drawn last. The ribbon reads the state of half the program -- which
// workspace is open, whether a file is selected, which toggles are on --
// and all of that is declared with let further up. Calling it before those
// lines run throws, and a throw at the top level of a script stops
// everything after it: the page renders, and not one button works.
try {
  moveDisplayControls();
  homeDisplayControls();
  renderRibbon();
} catch (e) {
  // Never let the ribbon take the rest of the program down with it.
  const bar = document.getElementById("ribbon-groups");
  if (bar) bar.textContent = "The ribbon could not be drawn: " + e.message;
}


// --------------------------------------------------------------------------
// The case map
// --------------------------------------------------------------------------
// MapLibre over the offline basemap, adapted from GLEAPP's gallery map (MIT;
// the map assets are under fme/web/ui/maps with their licences). Loaded the
// first time the map is opened, never with the page: a megabyte of script
// nobody needs until they ask for a map. Points carry a name and a grade,
// never a thumbnail, so a map is not a way round the review blur.
let caseMap = null, mapLibraryLoading = null;

function loadMapLibrary() {
  if (window.maplibregl && window.pmtiles) return Promise.resolve();
  if (mapLibraryLoading) return mapLibraryLoading;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/maps/maplibre-gl.css";
  document.head.appendChild(css);
  const script = src => new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.nonce = PAGE_NONCE;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`${src} did not load`));
    document.head.appendChild(el);
  });
  mapLibraryLoading = script("/maps/maplibre-gl.js")
    .then(() => script("/maps/pmtiles.js"))
    .then(() => {
      maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
    })
    .catch(e => { mapLibraryLoading = null; throw e; });
  return mapLibraryLoading;
}

async function caseMapStyle() {
  const d = await api("/api/basemap/style?flavor=dark");
  if (!d.ok) throw new Error(d.error || "no basemap");
  const s = d.style;
  // MapLibre wants the sprite and glyph addresses absolute; the server
  // hands out local paths, so this page's own origin goes in front here.
  for (const k of ["sprite", "glyphs"]) {
    if (typeof s[k] === "string" && s[k].startsWith("/")) s[k] = location.origin + s[k];
  }
  return s;
}

function closeCaseMap() {
  $("map-modal").classList.remove("open");
  if (caseMap) { caseMap.remove(); caseMap = null; }
}

function goToFileFromMap(relPath) {
  closeCaseMap();
  const file = lookup(relPath);
  if (file) { moveTo(file); return; }
  selectedPath = relPath;
  showDetails(relPath);
}

async function openCaseMap() {
  if (!jobId) return;
  $("map-info").textContent = "Loading…";
  $("map-modal").classList.add("open");
  let style, points;
  try {
    style = await caseMapStyle();
  } catch (e) {
    $("map-info").textContent = "No basemap to draw on. Import a .pmtiles or " +
      ".mbtiles file under Settings (Offline basemap for maps), then open " +
      "the map again.";
    return;
  }
  try {
    await loadMapLibrary();
    points = await api("/api/case/map", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job: jobId, state: filterState()})});
  } catch (e) {
    $("map-info").textContent = `The map could not be drawn: ${e.message}`;
    return;
  }
  const pts = points.points || [];
  $("map-info").textContent =
    `${count(pts.length)} of ${count(points.shown || 0)} file(s) the filter shows carry a location` +
    (points.truncated ? " (the first ones only; narrow the filter to see the rest)" : "") +
    (points.out_of_range ? ` · ${count(points.out_of_range)} with an impossible position, not drawn` : "") +
    (pts.length ? " · click a point to go to its file" : "");
  if (caseMap) { caseMap.remove(); caseMap = null; }
  caseMap = new maplibregl.Map({container: "case-map", style, center: [0, 20], zoom: 1,
                                attributionControl: {compact: false}});
  caseMap.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-right");
  caseMap.on("load", () => {
    const features = pts.map(p => ({type: "Feature",
      geometry: {type: "Point", coordinates: [p.lon, p.lat]},
      properties: {rel_path: p.rel_path, name: p.name,
                   graded: p.category !== null && p.category !== undefined &&
                           p.category !== uncategorizedCode ? 1 : 0}}));
    caseMap.addSource("files", {type: "geojson", cluster: true, clusterRadius: 40,
                                clusterMaxZoom: 16,
                                data: {type: "FeatureCollection", features}});
    caseMap.addLayer({id: "clusters", type: "circle", source: "files",
      filter: ["has", "point_count"],
      paint: {"circle-color": "#2f6fd8", "circle-opacity": 0.85,
              "circle-stroke-color": "#ffffff", "circle-stroke-width": 1,
              "circle-radius": ["step", ["get", "point_count"], 14, 25, 18, 250, 24]}});
    if (style.glyphs) {
      caseMap.addLayer({id: "cluster-count", type: "symbol", source: "files",
        filter: ["has", "point_count"],
        layout: {"text-field": ["get", "point_count_abbreviated"],
                 "text-font": ["Noto Sans Medium"], "text-size": 12},
        paint: {"text-color": "#ffffff"}});
    }
    caseMap.addLayer({id: "files", type: "circle", source: "files",
      filter: ["!", ["has", "point_count"]],
      paint: {"circle-radius": 6,
              "circle-color": ["case", ["==", ["get", "graded"], 1], "#e0a020", "#e74c3c"],
              "circle-stroke-color": "#ffffff", "circle-stroke-width": 1}});
    if (features.length) {
      const b = new maplibregl.LngLatBounds(features[0].geometry.coordinates,
                                            features[0].geometry.coordinates);
      features.forEach(f => b.extend(f.geometry.coordinates));
      caseMap.fitBounds(b, {padding: 60, maxZoom: 15, duration: 0});
    }
    caseMap.on("click", "clusters", async e => {
      const hit = e.features[0];
      const zoom = await caseMap.getSource("files")
        .getClusterExpansionZoom(hit.properties.cluster_id);
      caseMap.easeTo({center: hit.geometry.coordinates, zoom});
    });
    caseMap.on("click", "files", e => {
      const pr = e.features[0].properties;
      // Built from elements, not markup: the name is the suspect's.
      const box = document.createElement("div");
      const name = document.createElement("div");
      name.textContent = pr.name || pr.rel_path;
      name.style.cssText = "word-break:break-all;margin-bottom:6px;color:#222;";
      const go = document.createElement("button");
      go.textContent = "Go to this file";
      go.addEventListener("click", () => goToFileFromMap(pr.rel_path));
      box.append(name, go);
      new maplibregl.Popup({maxWidth: "260px"})
        .setLngLat(e.features[0].geometry.coordinates).setDOMContent(box).addTo(caseMap);
    });
    for (const layer of ["clusters", "files"]) {
      caseMap.on("mouseenter", layer, () => { caseMap.getCanvas().style.cursor = "pointer"; });
      caseMap.on("mouseleave", layer, () => { caseMap.getCanvas().style.cursor = ""; });
    }
  });
}
$("map-close").addEventListener("click", closeCaseMap);


// --------------------------------------------------------------------------
// Wellness: grayscale and masks (v2026.11.317)
// --------------------------------------------------------------------------
// Griffeye offers both. Grayscale is a way of looking; a mask covers part of
// one picture wherever this program shows it -- the full-size view and the
// details pane -- kept with the case so the next session sees it too. Neither
// touches a file, an export or a report.
function grayOn() { return $("gray-on").checked; }
function applyGray() { document.body.classList.toggle("wellness-gray", grayOn()); }
$("gray-on").checked = recall("gray", "0") === "1";
applyGray();
$("gray-on").addEventListener("change", () => {
  remember("gray", grayOn() ? "1" : "0");
  applyGray();
});

let fullFile = null, masking = false, maskStart = null, maskBox = null;
// Kept per case and file: keyed by the file alone, one case's masks were
// shown on the same picture in the next case, and saved into it with the
// next box drawn (v2026.11.317).
const maskCache = {};
const maskKey = sha256 => `${jobId}:${sha256}`;

async function loadMasks(sha256) {
  if (!jobId || !sha256) return [];
  const key = maskKey(sha256);
  if (maskCache[key]) return maskCache[key];
  try {
    const d = await api(`/api/case/masks?job=${encodeURIComponent(jobId)}` +
                        `&sha256=${encodeURIComponent(sha256)}`);
    maskCache[key] = d.masks || [];
  } catch (e) { maskCache[key] = []; }
  return maskCache[key];
}

async function saveMasks(sha256, list) {
  const job = jobId;
  const d = await api("/api/case/masks", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({job, sha256, masks: list})});
  maskCache[`${job}:${sha256}`] = d.masks || [];
  return d.masks || [];
}

function drawMasks(container, list) {
  const layer = container && container.querySelector(".maskwrap .masks");
  if (!layer) return;
  layer.innerHTML = "";
  for (const [left, top, width, height] of list || []) {
    const box = document.createElement("div");
    box.className = "mask";
    box.style.left = `${left * 100}%`; box.style.top = `${top * 100}%`;
    box.style.width = `${width * 100}%`; box.style.height = `${height * 100}%`;
    layer.appendChild(box);
  }
}

function setMasking(on) {
  masking = !!on;
  $("mask-draw").classList.toggle("on", masking);
  $("full-body").classList.toggle("masking", masking);
}
$("mask-draw").addEventListener("click", () => setMasking(!masking));
$("mask-clear").addEventListener("click", async () => {
  if (!fullFile) return;
  drawMasks($("full-body"), await saveMasks(fullFile.sha256, []));
  if (selectedPath === fullFile.rel_path) drawMasks($("details"), []);
});

function maskPoint(e) {
  const picture = $("full-body").querySelector(".maskwrap img, .maskwrap video");
  const box = picture.getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)),
          Math.min(1, Math.max(0, (e.clientY - box.top) / box.height))];
}
$("full-body").addEventListener("mousedown", e => {
  if (!masking || !$("full-body").querySelector(".maskwrap")) return;
  e.preventDefault(); e.stopPropagation();
  maskStart = maskPoint(e);
  maskBox = document.createElement("div");
  maskBox.className = "mask drawing";
  $("full-body").querySelector(".maskwrap .masks").appendChild(maskBox);
}, true);
document.addEventListener("mousemove", e => {
  if (!maskStart || !maskBox) return;
  const [x, y] = maskPoint(e);
  const left = Math.min(x, maskStart[0]), top = Math.min(y, maskStart[1]);
  maskBox.style.left = `${left * 100}%`; maskBox.style.top = `${top * 100}%`;
  maskBox.style.width = `${Math.abs(x - maskStart[0]) * 100}%`;
  maskBox.style.height = `${Math.abs(y - maskStart[1]) * 100}%`;
});
document.addEventListener("mouseup", async e => {
  if (!maskStart || !fullFile) return;
  // The file the box was drawn on, held before anything is awaited: read
  // again afterwards, a move to the next picture in between saved this
  // picture's masks onto that one.
  const target = fullFile;
  const [x, y] = maskPoint(e);
  const region = [Math.min(x, maskStart[0]), Math.min(y, maskStart[1]),
                  Math.abs(x - maskStart[0]), Math.abs(y - maskStart[1])];
  maskStart = null; maskBox = null;
  const list = (await loadMasks(target.sha256)).concat([region]);
  const saved = await saveMasks(target.sha256, list);
  if (fullFile === target) drawMasks($("full-body"), saved);
  if (selectedPath === target.rel_path) drawMasks($("details"), saved);
});


// --------------------------------------------------------------------------
// Storyboard (v2026.11.317)
// --------------------------------------------------------------------------
// A clip as a grid of evenly spaced frames, as Griffeye shows one: the
// whole video at a glance, so most of it need not be watched. Click a frame
// to open the video at that moment. The review blur, grayscale and the
// hide-screen key apply to it as to every picture.
async function openStoryboard(relPath) {
  const file = lookup(relPath) || {rel_path: relPath, name: relPath.split("/").pop(),
                                   category: "video"};
  $("story-title").textContent = `Storyboard — ${file.name || relPath}`;
  $("story-grid").innerHTML = '<span class="hint">decoding frames…</span>';
  $("story-grid").classList.toggle("blurred", blurOn());
  $("story-modal").classList.add("open");
  try {
    const d = await api(`/api/storyboard?job=${jobId}&file=${encodeURIComponent(relPath)}`);
    if (!d.available) { $("story-grid").innerHTML = `<span class="hint">${esc(d.message)}</span>`; return; }
    if (!d.frames.length) {
      $("story-grid").innerHTML = `<span class="hint">No frame could be decoded` +
        `${d.error ? ` — ${esc(d.error)}` : ""}.</span>`;
      return;
    }
    const clock = t => t === null || t === undefined ? "" :
      `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
    $("story-grid").innerHTML = d.frames.map(f =>
      `<figure class="storyframe" data-at="${f.at ?? ""}"><img src="${f.src}" alt="">` +
      `<figcaption>${clock(f.at)}</figcaption></figure>`).join("");
    $("story-grid").querySelectorAll(".storyframe").forEach(el =>
      el.addEventListener("click", () => {
        const at = parseFloat(el.dataset.at);
        $("story-modal").classList.remove("open");
        viewFullSize(file);
        const video = $("full-body").querySelector("video");
        if (video && !isNaN(at)) {
          video.dataset.startAt = String(at);
          const seek = () => { video.currentTime = at; };
          if (video.readyState >= 1) seek();
          else video.addEventListener("loadedmetadata", seek, {once: true});
        }
      }));
  } catch (e) { $("story-grid").innerHTML = `<span class="hint">${esc(e.message)}</span>`; }
}
$("story-close").addEventListener("click", () => $("story-modal").classList.remove("open"));
