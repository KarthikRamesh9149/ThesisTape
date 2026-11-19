const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const labels = {
  complete: "Complete",
  withheld: "Withheld",
  qualified: "Qualified",
  insufficient_evidence: "Insufficient evidence",
  queued: "Queued",
  running: "Running",
  failed: "Stopped",
  paused: "Paused",
  needs_reconciliation: "Needs reconciliation",
};
const money = (n) =>
  "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 10 });
const day = (s) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(s.slice(0, 10) + "T00:00:00Z"));
function cutoff(s) {
  if (s.length === 10) return day(s) + " (end of UTC day)";
  const d = new Date(s).toISOString();
  const fraction = s.match(/\.\d+(?=Z|[+-]\d\d:\d\d$)/)?.[0] || "";
  const time = d.slice(11, fraction || d.slice(17, 19) !== "00" ? 19 : 16) + fraction;
  return day(d) + ", " + time + " UTC";
}
let key = "",
  token = "",
  info,
  tab = "memo",
  chosenTab = false,
  selected = 0;
let epoch = 0,
  sourceEpoch = 0,
  refreshId = 0,
  poll,
  submitting = false;
const path = () => "/api/runs/" + encodeURIComponent(key);

async function request(url, body) {
  const options =
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Desk-Token": token },
          body: JSON.stringify(body),
        };
  const res = await fetch(url, { ...options, cache: "no-store" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}
function notice(id, msg) {
  $(id).textContent = msg || "";
  $(id).hidden = !msg;
}
function showTab(next, explicit = true) {
  tab = next;
  if (explicit) chosenTab = true;
  for (const k of ["memo", "activity"]) {
    const active = k === tab;
    $(k + "-tab").setAttribute("aria-selected", String(active));
    $(k + "-tab").tabIndex = active ? 0 : -1;
    $(k).hidden = !active;
  }
}
function setStatus(r) {
  const text = r.pausing
    ? "Pausing"
    : r.state === "complete" && r.coverage === "qualify"
      ? "Qualified"
      : labels[r.state] || r.state;
  $("status").textContent = text;
  $("status").dataset.tone =
    text === "Complete"
      ? "good"
      : ["Running", "Queued", "Pausing"].includes(text)
        ? "active"
        : "attention";
  $("control").hidden = !r.can_pause && !r.can_resume;
  $("control").textContent = r.can_pause ? "Pause run" : "Resume run";
  $("control").disabled = Boolean(r.pausing);
  let message = r.operator_error || r.failure;
  if (r.state === "needs_reconciliation")
    message =
      "A provider call is unconfirmed. " +
      money(r.reserved) +
      " remains reserved until a receipt is available.";
  if (r.state === "withheld") message = "Independent review withheld this report.";
  notice("run-error", message);
}
function emptyText(r) {
  if (r.state === "insufficient_evidence") return "The dated searches found no eligible evidence for this question.";
  if (r.state === "withheld") return "The review did not authorize a memo for export.";
  if (r.state === "failed" || r.state === "needs_reconciliation")
    return "No memo was released. Run activity shows the last recorded assignment.";
  if (r.state === "paused") return "This run is paused. Its completed assignments are saved.";
  return "The memo will appear here when the run releases it.";
}
function addTokens(node, tokens) {
  for (const part of tokens) {
    if (typeof part === "string") {
      node.append(document.createTextNode(part));
      continue;
    }
    if (part.strong !== undefined) {
      node.append(el("strong", part.strong));
      continue;
    }
    if (part.ref) {
      const b = el("button", "[" + part.ref + "]", "cite");
      b.type = "button";
      b.dataset.ref = part.ref;
      b.setAttribute("aria-label", "Inspect source " + part.ref + ", " + part.label);
      b.title = part.label;
      b.onclick = () => chooseSource(part.ref, true);
      node.append(b);
    } else {
      const a = el("a", part.label);
      a.href = part.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      node.append(a);
    }
  }
}
function renderMemo(r) {
  const box = $("memo");
  box.replaceChildren();
  if (!r.report) {
    box.append(el("p", emptyText(r), "empty"));
    return;
  }
  let list;
  for (const block of r.report) {
    if (block.kind !== "bullet") list = null;
    if (block.kind === "table") {
      const wrap = el("div", undefined, "table-wrap"),
        table = el("table");
      block.rows.forEach((row, i) => {
        const tr = table.insertRow();
        row.forEach((cell) => {
          const td = el(i === 0 ? "th" : "td");
          if (i === 0) td.scope = "col";
          addTokens(td, cell);
          tr.append(td);
        });
      });
      wrap.append(table);
      box.append(wrap);
    } else {
      const item = el(block.kind === "heading" ? "h3" : block.kind === "bullet" ? "li" : "p");
      addTokens(item, block.tokens);
      if (block.kind === "bullet") {
        if (!list) {
          list = el("ul");
          box.append(list);
        }
        list.append(item);
      } else box.append(item);
    }
  }
}
const phases = {
  plan: "Plan assignments",
  decide: "Choose next action",
  search: "Search the archive",
  extract: "Extract evidence",
  memo: "Write memo",
  memo_repair: "Repair memo",
  revise: "Revise memo",
  initial: "Review claims",
  recheck: "Recheck revision",
  adjudicate: "Assess dispute",
  analyze: "Analyze forecast",
  read: "Read table",
  after_image: "Check table reading",
};
function renderActivity(r) {
  const box = $("activity");
  box.replaceChildren();
  if (r.activity.length) {
    const wrap = el("div", undefined, "table-wrap"),
      table = el("table"),
      header = table.createTHead().insertRow();
    for (const label of ["Assignment", "State", "Estimated cost"]) {
      const h = el("th", label);
      h.scope = "col";
      header.append(h);
    }
    const body = table.createTBody();
    for (const c of r.activity) {
      const row = body.insertRow(),
        stage = c.stage.replace(/^followup\./, "").split(".");
      const title = row.insertCell(),
        state = row.insertCell(),
        cost = row.insertCell();
      title.append(
        el("b", c.role),
        el("small", (phases[stage[1]] || stage.slice(1).join(" ")) + " · " + c.model),
      );
      state.textContent = c.state;
      state.className = "activity-state" + (c.state === "In flight" ? " live" : "");
      cost.textContent = c.cost == null ? "—" : money(c.cost);
      if (c.cost == null && c.reserved) cost.append(el("small", money(c.reserved) + " reserved"));
    }
    wrap.append(table);
    box.append(wrap);
  } else box.append(el("p", "No model calls have been dispatched.", "empty"));
  if (r.decisions.length) {
    const section = el("section", undefined, "decisions");
    section.append(el("h3", "Routing decisions"));
    for (const d of r.decisions) {
      const row = el("div", undefined, "decision");
      row.append(el("b", d.label));
      if (d.detail) row.append(el("p", d.detail));
      section.append(row);
    }
    box.append(section);
  }
}
function markSource() {
  document
    .querySelectorAll("[data-ref]")
    .forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.ref) === selected)));
}
async function chooseSource(n, focus = false) {
  selected = n;
  markSource();
  const generation = ++sourceEpoch,
    current = key,
    panel = $("passage");
  panel.setAttribute("aria-busy", "true");
  panel.replaceChildren(el("p", "Opening dated passage…", "empty"));
  try {
    const p = await request(path() + "/passages/" + n);
    if (generation !== sourceEpoch || key !== current) return;
    const card = el("div", undefined, "passage-card"),
      quote = el("blockquote", p.text, "passage-text");
    if (p.text.length <= 900) quote.classList.add("short-passage");
    else quote.tabIndex = 0;
    quote.setAttribute("aria-label", "Original passage text");
    const foot = el("div", undefined, "source-footer"),
      a = el("a", "Open original ↗");
    a.href = p.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    foot.append(
      el("strong", p.title),
      el(
        "p",
        (new URL(p.url).hostname === "www.sec.gov" ? "Filed " : "Available by ") + day(p.published),
      ),
      el("p", p.locator),
      a,
    );
    card.append(quote, foot);
    panel.replaceChildren(card);
    if (focus) {
      panel.focus({ preventScroll: true });
      if (window.matchMedia("(max-width: 800px)").matches) panel.scrollIntoView({ block: "start" });
    }
  } catch (err) {
    if (generation === sourceEpoch) panel.replaceChildren(el("p", err.message, "notice error"));
  } finally {
    if (generation === sourceEpoch) panel.removeAttribute("aria-busy");
  }
}
function renderSources(r) {
  $("passage-count").textContent =
    r.sources.length + (r.sources.length === 1 ? " passage" : " passages");
  const list = $("source-list");
  list.replaceChildren();
  if (!r.sources.length) {
    ++sourceEpoch;
    selected = 0;
    $("passage").removeAttribute("aria-busy");
    $("passage").replaceChildren(
      el(
        "p",
        r.report
          ? "This memo has no cited passages."
          : "Cited passages appear with the released memo.",
        "empty",
      ),
    );
    return;
  }
  for (const s of r.sources) {
    const b = el("button", undefined, "source-row");
    b.type = "button";
    b.dataset.ref = s.number;
    b.title = s.title;
    b.append(el("span", String(s.number), "source-no"), el("span", s.locator));
    b.onclick = () => chooseSource(s.number);
    list.append(b);
  }
  chooseSource(selected || r.sources[0].number);
}
function render(r) {
  const old = info;
  info = r;
  $("run").hidden = false;
  $("loading").hidden = true;
  document.title = r.title + " · ThesisTape";
  $("run-title").textContent = r.title;
  $("cutoff").textContent = "Cutoff " + cutoff(r.cutoff);
  $("cutoff").title = r.cutoff;
  $("route").textContent = r.route;
  $("question").textContent = r.question;
  $("spent").textContent = money(r.spent);
  $("budget").textContent = money(r.budget);
  $("calls").textContent = r.call_count;
  $("reserved").hidden = Number(r.reserved) === 0;
  $("reserved").textContent = money(r.reserved) + " reserved";
  setStatus(r);
  $("export").hidden = !r.report;
  if (r.report) $("export").href = path() + "/memo.md";
  else $("export").removeAttribute("href");
  if (!chosenTab) showTab(r.report ? "memo" : "activity", false);
  if (!old || old.state !== r.state || Boolean(old.report) !== Boolean(r.report)) {
    renderMemo(r);
    renderSources(r);
  }
  if (
    !old ||
    JSON.stringify([old.activity, old.decisions]) !== JSON.stringify([r.activity, r.decisions])
  )
    renderActivity(r);
}
async function refresh(sequence = epoch) {
  const id = ++refreshId;
  clearTimeout(poll);
  try {
    const r = await request(path());
    if (sequence !== epoch || id !== refreshId) return;
    notice("global-error", "");
    $("retry").hidden = true;
    render(r);
    if (r.active || ["running", "queued"].includes(r.state)) {
      poll = setTimeout(() => refresh(sequence), 650);
    }
  } catch (err) {
    if (sequence !== epoch || id !== refreshId) return;
    $("loading").hidden = true;
    $("run").hidden = true;
    info = undefined;
    notice("global-error", err.message);
    $("retry").hidden = false;
  }
}
function openRun(id) {
  if (key === id && info) return;
  key = id;
  ++epoch;
  ++sourceEpoch;
  clearTimeout(poll);
  info = undefined;
  chosenTab = false;
  selected = 0;
  $("retry").hidden = true;
  notice("global-error", "");
  $("run").hidden = true;
  $("loading").hidden = false;
  if (location.hash !== "#run=" + id) history.replaceState(null, "", "#run=" + id);
  refresh(epoch);
}
const hashRun = () => /^#run=([a-z0-9-]+)$/.exec(location.hash)?.[1];
window.addEventListener("hashchange", () => {
  if (hashRun()) openRun(hashRun());
});
for (const k of ["memo", "activity"]) {
  const tabButton = $(k + "-tab");
  tabButton.onclick = () => showTab(k);
  tabButton.onkeydown = (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const dest =
      e.key === "Home" ? "memo" : e.key === "End" ? "activity" : k === "memo" ? "activity" : "memo";
    showTab(dest);
    $(dest + "-tab").focus();
  };
}
$("control").onclick = async () => {
  const current = key;
  $("control").disabled = true;
  try {
    await request(path() + (info.can_pause ? "/pause" : "/resume"), {});
    if (key === current) await refresh();
  } catch (err) {
    if (key === current) {
      notice("run-error", err.message);
      $("control").disabled = false;
    }
  }
};
function libraryRows(runs) {
  const box = $("library");
  box.replaceChildren();
  for (const [name, values] of [
    ["Your runs", runs.filter((r) => !r.example)],
    ["Saved examples", runs.filter((r) => r.example)],
  ]) {
    if (!values.length) continue;
    box.append(el("h3", name));
    for (const r of values) {
      const b = el("button", undefined, "library-row");
      b.type = "button";
      b.append(
        el("b", r.title),
        el("span", r.question, "library-question"),
        el("small", day(r.cutoff) + " · " + (labels[r.state] || r.state)),
      );
      b.onclick = () => {
        $("library-dialog").close();
        openRun(r.id);
      };
      box.append(b);
    }
  }
}
$("library-open").onclick = async () => {
  $("library").replaceChildren(el("p", "Loading saved runs…", "empty"));
  $("library-dialog").showModal();
  try {
    libraryRows((await request("/api/runs")).runs);
  } catch (err) {
    $("library").textContent = err.message;
  }
};
$("new-open").onclick = () => {
  notice("intake-error", "");
  $("new-dialog").showModal();
  $("research-question").focus();
};
document
  .querySelectorAll(".dismiss, .dismiss-form")
  .forEach((b) => (b.onclick = () => b.closest("dialog").close()));
let pending;
try {
  pending = JSON.parse(sessionStorage.getItem("desk-pending"));
} catch {
  pending = null;
}
$("intake").onsubmit = async (e) => {
  e.preventDefault();
  if (submitting) return;
  const question = $("research-question").value.trim(),
    time = $("new-time").disabled ? "" : $("new-time").value;
  if (!question) {
    notice("intake-error", "Enter a research question.");
    return;
  }
  const fields = {
    question,
    collection: $("collection").value,
    budget: $("new-budget").value,
    cutoff:
      $("new-cutoff").value + (time ? "T" + (time.length === 5 ? time + ":00" : time) + "Z" : ""),
  };
  const signature = JSON.stringify(fields);
  if (pending?.signature !== signature)
    pending = { signature, id: crypto.randomUUID().replaceAll("-", "") };
  try {
    sessionStorage.setItem("desk-pending", JSON.stringify(pending));
  } catch {
    /* A private browser can disable storage. */
  }
  submitting = true;
  $("start").disabled = true;
  $("start").textContent = "Starting…";
  notice("intake-error", "");
  try {
    const result = await request("/api/runs", { ...fields, request_id: pending.id });
    pending = null;
    try {
      sessionStorage.removeItem("desk-pending");
    } catch {
      /* Optional storage. */
    }
    $("new-dialog").close();
    openRun(result.id);
  } catch (err) {
    notice("intake-error", err.message);
  } finally {
    submitting = false;
    $("start").disabled = false;
    $("start").textContent = "Start research";
  }
};
async function init() {
  try {
    const s = await request("/api/session");
    token = s.token;
    for (const [value, label] of Object.entries(s.collections))
      $("collection").add(new Option(label, value));
    $("collection").onchange = () => {
      const byDay = $("collection").value === "historical-web";
      $("new-time").disabled = byDay;
      $("new-time").parentElement.hidden = byDay;
      $("cutoff-help").textContent = byDay
        ? "Public archives use the end of the selected UTC day."
        : "Leave the time blank to use the end of the UTC day.";
    };
    $("collection").value = "historical-web";
    $("collection").onchange();
    openRun(hashRun() || s.runs[0]?.id || "example-twitter");
  } catch (err) {
    $("loading").hidden = true;
    notice("global-error", err.message);
    $("retry").hidden = false;
  }
}
$("retry").onclick = () => (key ? refresh() : init());
init();
