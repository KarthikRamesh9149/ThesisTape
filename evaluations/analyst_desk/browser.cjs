// Run the read-only browser checks by default. --live submits one real case.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const question =
  "What did Tradeweb correct about the high end of its 2023 adjusted expense forecast, and what stayed unchanged in the amended filing?";
const cutoff = "2023-10-27T00:15:00Z";
const base = process.env.DESK_URL || "http://127.0.0.1:8765";
const output = process.env.DESK_QA_DIR || "runs/desk-qa";
const root = process.env.DESK_RUNS_DIR || "runs/desk";
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const hasMemo = async (page) => {
  await page.locator("#export").waitFor();
  await page.getByRole("tab", { name: "Research memo" }).click();
  await page.locator("#memo .cite").first().waitFor();
};

async function downloadAndCompare(page, directory) {
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export .md" }).click();
  const saved = await downloading;
  const bytes = await fs.readFile(await saved.path());
  const replay = execFileSync(process.env.DESK_PYTHON || ".venv/bin/python", [
    "-B",
    "-m",
    "scripts.research_team",
    "replay",
    "--run-dir",
    directory,
  ]);
  assert.deepEqual(bytes, replay);
  assert.deepEqual(bytes, await fs.readFile(path.join(directory, "memo.md")));
  return { bytes: bytes.length, sha256: digest(bytes) };
}

async function examples(page) {
  await page.goto(base + "/#run=example-twitter");
  await page.getByRole("heading", { name: "Twitter merger status" }).waitFor();
  await hasMemo(page);
  assert.match(await page.locator("#memo").innerText(), /^No\./);
  await page.getByRole("button", { name: /Inspect source 3,/ }).click();
  await page.waitForFunction(() =>
    document.querySelector("#passage blockquote")?.textContent.includes("$54.20"),
  );
  assert.match(
    await page.locator("#passage a").getAttribute("href"),
    /^https:\/\/www.sec.gov\/Archives\/.+#/,
  );
  assert.equal(await page.locator("#memo [data-ref='3']").getAttribute("aria-pressed"), "true");
  await page.screenshot({ path: path.join(output, "desktop.png") });
  await page.getByRole("tab", { name: "Research memo" }).press("ArrowRight");
  await page.getByRole("heading", { name: "Routing decisions" }).waitFor();
  assert.match(await page.locator("#activity").innerText(), /Dispute specialist/);
  const archived = await downloadAndCompare(page, "evaluations/material_dispute/trial/dispute");
  await page.getByRole("button", { name: "Saved runs" }).click();
  await page.getByRole("button", { name: /Disney and Hulu/ }).click();
  await page.getByRole("heading", { name: "Disney and Hulu" }).waitFor();
  await page.getByRole("heading", { name: "Arithmetic check" }).waitFor();
  await page.getByRole("button", { name: "New research" }).click();
  assert.equal(
    await page.locator("#research-question").evaluate((e) => e === document.activeElement),
    true,
  );
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.goto(base + "/#run=example-twitter");
  await hasMemo(page);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.getByRole("button", { name: /Inspect source 3,/ }).click();
    await page.waitForFunction(() =>
      document.querySelector("#passage blockquote")?.textContent.includes("$54.20"),
    );
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    assert.equal(
      await page.locator("#passage").evaluate((e) => e === document.activeElement),
      true,
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    if (width === 390) await page.screenshot({ path: path.join(output, "mobile.png") });
  }
  await page.setViewportSize({ width: 1536, height: 1024 });
  return archived;
}

async function live(page) {
  const snapshots = [],
    writes = [];
  page.on("response", async (res) => {
    const p = new URL(res.url()).pathname;
    if (res.request().method() === "POST") writes.push([p, res.status()]);
    if (res.request().method() !== "GET" || !/^\/api\/runs\/desk-[a-f0-9]+$/.test(p) || !res.ok())
      return;
    try {
      const r = await res.json();
      snapshots.push({
        state: r.state,
        reserved: r.reserved,
        calls: r.call_count,
        decisions: r.decisions.map((d) => d.label),
      });
    } catch {
      /* Page closed before this poll completed. */
    }
  });
  await page.getByRole("button", { name: "New research" }).click();
  await page.getByLabel("Research question").fill(question);
  await page.getByLabel("Source collection").selectOption("archive");
  await page.getByLabel("Cutoff date (UTC)").fill(cutoff.slice(0, 10));
  await page.getByLabel("Time in UTC").fill("00:15:00");
  await page.getByLabel("Spending limit (USD)").fill("0.10");
  const accepted = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/runs" && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start research" }).click();
  const res = await accepted;
  assert.equal(res.status(), 202, "Intake was not accepted");
  const { id } = await res.json();
  console.log("Accepted", id);
  const directory = path.join(root, id);
  const active = page.locator("#activity .live").first();
  await active.waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "Pause run" }).click();
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "Paused",
    null,
    { timeout: 120000 },
  );
  const paused = JSON.parse(await fs.readFile(path.join(directory, "run.json")));
  assert.equal(paused.state, "paused");
  assert.ok(paused.events.some(([event]) => event === "settle"));
  await page.screenshot({ path: path.join(output, "paused.png") });
  await page.getByRole("button", { name: "Resume run" }).click();
  await page.waitForFunction(
    () =>
      ["Complete", "Qualified", "Stopped", "Withheld", "Needs reconciliation"].includes(
        document.querySelector("#status").textContent,
      ),
    null,
    { timeout: 300000 },
  );
  assert.equal(
    await page.locator("#status").innerText(),
    "Complete",
    await page.locator("#run-error").textContent(),
  );
  assert.equal(await page.locator("#cutoff").getAttribute("title"), cutoff);
  const saved = await fs.readFile(path.join(directory, "run.json"));
  const record = JSON.parse(saved);
  assert.equal(record.question, question);
  assert.equal(record.budget_usd, "0.10");
  assert.ok(record.resumptions.length === 1);
  for (const [stage, value] of Object.entries(paused.calls))
    assert.deepEqual(record.calls[stage], value);
  await hasMemo(page);
  const memo = await page.locator("#memo").innerText();
  assert.match(memo, /695/);
  assert.match(memo, /659/);
  assert.match(
    memo,
    /unchanged|no other changes|no other (?:information|change)|otherwise un(?:changed|modified)/i,
  );
  await page
    .getByRole("button", { name: /Inspect source \d+, .*Item 2\.02/i })
    .first()
    .click();
  await page.waitForFunction(() =>
    /695/.test(document.querySelector("#passage blockquote")?.textContent || ""),
  );
  const passage = await page.locator("#passage blockquote").innerText();
  assert.match(passage, /659/);
  const url = await page.locator("#passage a").getAttribute("href");
  assert.match(url, /\/000175873023000171\/tw-20231026.htm#/);
  const exported = await downloadAndCompare(page, directory);
  await page.screenshot({ path: path.join(output, "tradeweb.png"), fullPage: true });
  assert.deepEqual(await fs.readFile(path.join(directory, "run.json")), saved);
  const observedStates = [...new Set(snapshots.map((s) => s.state))];
  assert.ok(
    observedStates.includes("running") &&
      observedStates.includes("paused") &&
      observedStates.includes("complete"),
  );
  assert.ok(snapshots.some((s) => Number(s.reserved) > 0));
  const result = {
    id,
    question,
    cutoff,
    observed_states: observedStates,
    saw_reservation: true,
    settled_at_pause: paused.events
      .filter(([event]) => event === "settle")
      .map(([, stage]) => stage),
    writes,
    visible_source_url: url,
    exported,
    calls: Object.keys(record.calls).length,
    spend_usd: record.estimated_model_cost_usd,
    unchanged_after_inspection: true,
  };
  await fs.writeFile(path.join(output, "browser.json"), JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
}

async function reopenProof(page) {
  const proof = "evaluations/analyst_desk/proof";
  const observed = JSON.parse(await fs.readFile(path.join(proof, "browser.json")));
  const writes = [];
  page.on("request", (r) => {
    if (r.method() === "POST") writes.push(r.url());
  });
  await page.goto(base + "/#run=example-tradeweb-desk");
  await page.waitForFunction(
    (q) => document.getElementById("question").textContent === q,
    question,
  );
  await hasMemo(page);
  assert.equal(await page.locator("#cutoff").getAttribute("title"), cutoff);
  assert.equal(await page.locator("#spent").innerText(), "$" + observed.spend_usd);
  await page.getByRole("tab", { name: "Run activity" }).click();
  await page.getByText("Release reviewed memo", { exact: true }).waitFor();
  const activity = await page.locator("#activity").innerText();
  assert.match(activity, /Write the memo/);
  assert.match(activity, /Review claims/);
  await page.getByRole("tab", { name: "Research memo" }).click();
  await page
    .getByRole("button", { name: /Inspect source \d+, .*Item 2\.02/i })
    .first()
    .click();
  await page.waitForFunction(() =>
    /695/.test(document.querySelector("#passage blockquote")?.textContent || ""),
  );
  assert.equal(await page.locator("#passage a").getAttribute("href"), observed.visible_source_url);
  assert.deepEqual(await downloadAndCompare(page, proof), observed.exported);
  assert.equal(
    await page.locator("#passage blockquote").evaluate((e) => e.scrollHeight <= e.clientHeight),
    true,
  );
  await page.screenshot({ path: path.join(output, "tradeweb.png"), fullPage: true });
  assert.deepEqual(writes, []);
  console.log(
    "Reopened the live proof, the approval, the primary passage and the identical download with zero writes.",
  );
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH,
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1536, height: 1024 },
      acceptDownloads: true,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    const archived = await examples(page);
    console.log(
      "Saved runs, citations, keyboard tabs, two mobile sizes and CLI download passed.",
      archived,
    );
    if (process.argv.includes("--live")) await live(page);
    else if (process.argv.includes("--proof")) await reopenProof(page);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
