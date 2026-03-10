/**
 * LESSON 7: AI-Powered Browser Automation with computer-use-preview
 *
 * How this works:
 *  1. We launch Playwright and take a screenshot of the page
 *  2. We send that screenshot + a task description to the Azure OpenAI
 *     computer-use-preview model (authenticated via Entra ID)
 *  3. The model looks at the screenshot and returns ACTIONS to perform
 *     (click at x/y, type text, scroll, etc.)
 *  4. We execute those actions in Playwright, take a new screenshot
 *  5. We send the new screenshot back to the model ("here's what happened")
 *  6. Repeat (agentic loop) until the model is done and returns a text summary
 *
 * Authentication: Entra ID via DefaultAzureCredential
 *   Run `az login` in your terminal before running this script.
 *   The credential automatically picks up your Azure CLI session.
 *
 * Run: npx ts-node src/05-ai-agent/computer-use-agent.ts
 */

import { chromium, Page } from "playwright";
import { DefaultAzureCredential, getBearerTokenProvider } from "@azure/identity";
import { AzureOpenAI } from "openai";
import * as fs from "fs";
import * as path from "path";

// ── Telemetry helpers ─────────────────────────────────────────────────────
const runStart = Date.now();

function elapsed(): string {
  return `+${((Date.now() - runStart) / 1000).toFixed(2)}s`;
}

function banner(title: string) {
  const line = "─".repeat(50);
  console.log(`\n${line}`);
  console.log(`  ${title}`);
  console.log(line);
}

function logTelemetry(label: string, data: Record<string, unknown>) {
  const entries = Object.entries(data)
    .map(([k, v]) => `    ${k.padEnd(22)}: ${v}`)
    .join("\n");
  console.log(`  [telemetry] ${label}\n${entries}`);
}

// ── Config ─────────────────────────────────────────────────────────────────
// Values are read from environment variables. Copy .env.example → .env and populate.
const APP_URL = process.env.ZAVA_AIR_URL ?? "http://localhost:3000/";
const AZURE_ENDPOINT = process.env.AZURE_OPENAI_BASE_URL ?? "";
const DEPLOYMENT = process.env.AZURE_OPENAI_DEPLOYMENT ?? "computer-use-preview";
const API_VERSION = "2025-03-01-preview";

// gpt-4o-mini — used only for cheap intent extraction (no screenshots)
const MINI_DEPLOYMENT = "gpt-4o-mini";
const MINI_API_VERSION = "2024-05-01-preview";

// ── User task (change this to anything you like) ─────────────────────────
// The AI will figure out which filters to apply from natural language.
const USER_TASK = process.argv[2] ??
  "Give me a summary of all escalated critical complaints";

// ── Known filter options on the page ─────────────────────────────────────
// These mirror the <option value="..."> attributes in the HTML.
const FILTER_OPTIONS = {
  severity: ["", "Low", "Medium", "High", "Critical"],
  status:   ["", "Open", "Under Review", "Resolved", "Closed", "Escalated"],
} as const;

// ── What extractFilters() returns ────────────────────────────────────────
interface FilterIntent {
  severity: string; // "" means All
  status:   string; // "" means All
  reasoning: string;
}

const DISPLAY_WIDTH = 1280;
const DISPLAY_HEIGHT = 800;
const MAX_STEPS = 25; // safety limit on agentic loop iterations

const OUT = path.resolve(__dirname, "../screenshots/ai-agent");

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Step 1 of the pipeline: cheap structured call to extract filter intent.
 * Uses gpt-4o-mini (chat completions) — no screenshots, very few tokens.
 * Returns the severity/status values Playwright should set.
 */
async function extractFilters(
  aiClient: AzureOpenAI,
  task: string
): Promise<FilterIntent> {
  const systemPrompt = `
You are a filter-intent extractor for a customer complaints dashboard.
The page has two dropdown filters:
  - Severity: ${ FILTER_OPTIONS.severity.map(v => v || "(All)").join(" | ") }
  - Status:   ${ FILTER_OPTIONS.status.map(v => v || "(All)").join(" | ") }

Given the user's task, return ONLY valid JSON in this exact shape:
{
  "severity": "<exact option value or empty string for All>",
  "status":   "<exact option value or empty string for All>",
  "reasoning": "<one sentence explaining your choice>"
}
Return nothing else — no markdown, no explanation outside the JSON.`.trim();

  const completion = await (aiClient as any).chat.completions.create({
    model: MINI_DEPLOYMENT,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user",   content: task },
    ],
    temperature: 0,
    max_tokens: 200,
  });

  const raw = completion.choices[0]?.message?.content?.trim() ?? "{}";
  try {
    const parsed = JSON.parse(raw) as FilterIntent;
    // Validate against known options — fall back to "" (All) if unknown
    parsed.severity = FILTER_OPTIONS.severity.includes(parsed.severity as any)
      ? parsed.severity : "";
    parsed.status = FILTER_OPTIONS.status.includes(parsed.status as any)
      ? parsed.status : "";
    return parsed;
  } catch {
    console.warn("  [intent]   ⚠ Could not parse filter JSON, defaulting to All/All");
    return { severity: "", status: "", reasoning: "parse error" };
  }
}

/** Applies extracted filters via Playwright and waits for DOM to update */
async function applyFilters(page: Page, intent: FilterIntent): Promise<void> {
  // Helper: select + wait for networkidle + wait for rows to exist
  const applyOne = async (selector: string, value: string, label: string) => {
    if (value === "") {
      console.log(`  [filter]   ${label} = (All) — skipping`);
      return;
    }
    await page.locator(selector).selectOption(value);
    await page.waitForLoadState("networkidle");
    await page.waitForFunction(() => {
      const rows = document.querySelectorAll(
        "table tbody tr, .complaint-item, .complaint-row, [data-complaint]"
      );
      return rows.length >= 0; // just wait for DOM to settle
    }, { timeout: 10000 }).catch(() => {});
    console.log(`  [filter]   ${label} = ${value}  ${elapsed()}`);
  };

  await applyOne("#filterSeverity", intent.severity, "Severity");
  await applyOne("#filterStatus",   intent.status,   "Status");
  await page.waitForTimeout(800); // let final render settle
}

/** Captures the current page as a base64 PNG string */
async function takeScreenshot(page: Page, stepLabel?: string): Promise<string> {
  const t = Date.now();
  const buffer = await page.screenshot({ fullPage: false });
  const kb = (buffer.length / 1024).toFixed(1);
  if (stepLabel) {
    await fs.promises.writeFile(path.join(OUT, `${stepLabel}.png`), buffer);
    console.log(`  [screenshot] ${stepLabel}.png  (${kb} KB, ${Date.now() - t}ms)  ${elapsed()}`);
  }
  return buffer.toString("base64");
}

/**
 * Executes a single computer-use action returned by the model.
 * Supported action types mirror what computer-use-preview can emit.
 */
async function executeAction(page: Page, action: Record<string, any>): Promise<void> {
  const t = Date.now();
  switch (action.type) {
    case "click":
      console.log(`  [action] CLICK        (${action.x}, ${action.y})  button=${action.button ?? "left"}`);
      await page.mouse.click(action.x, action.y, { button: action.button ?? "left" });
      break;

    case "double_click":
      console.log(`  [action] DOUBLE_CLICK (${action.x}, ${action.y})`);
      await page.mouse.dblclick(action.x, action.y);
      break;

    case "scroll":
      console.log(`  [action] SCROLL       (${action.x}, ${action.y})  dir=${action.scroll_direction}  dist=${action.scroll_distance ?? 100}`);
      await page.mouse.move(action.x, action.y);
      await page.mouse.wheel(
        action.scroll_direction === "right" ? action.scroll_distance ?? 100 : 0,
        action.scroll_direction === "down"  ?  (action.scroll_distance ?? 100) :
        action.scroll_direction === "up"    ? -(action.scroll_distance ?? 100) : 0
      );
      break;

    case "type":
      console.log(`  [action] TYPE         "${action.text}"`);
      await page.keyboard.type(action.text);
      break;

    case "key":
    case "keypress":  // model sometimes emits "keypress" instead of "key"
      console.log(`  [action] KEY          "${action.key}"`);
      await page.keyboard.press(action.key);
      break;

    case "wait": {
      const ms = action.duration ?? action.ms ?? 1000;
      console.log(`  [action] WAIT         ${ms}ms`);
      await page.waitForTimeout(ms);
      break;
    }

    case "screenshot":
      console.log("  [action] SCREENSHOT   (model requested — will capture after step)");
      break;

    default:
      console.log(`  [action] UNKNOWN      type=${action.type} — skipping`);
  }
  console.log(`  [action] ↳ completed in ${Date.now() - t}ms  ${elapsed()}`);
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  // Wipe the output folder so each run starts clean
  await fs.promises.rm(OUT, { recursive: true, force: true });
  await fs.promises.mkdir(OUT, { recursive: true });
  console.log(`  [cleanup]  Cleared ${OUT}`);

  banner("INIT — Azure OpenAI + Browser");
  logTelemetry("config", {
    endpoint:      AZURE_ENDPOINT,
    deployment:    DEPLOYMENT,
    apiVersion:    API_VERSION,
    display:       `${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}`,
    maxSteps:      MAX_STEPS,
    outputDir:     OUT,
  });

  // ── 1. Azure OpenAI client with Entra ID authentication ──────────────────
  const credential = new DefaultAzureCredential();
  const tokenProvider = getBearerTokenProvider(
    credential,
    "https://cognitiveservices.azure.com/.default"
  );
  console.log(`  [auth]     DefaultAzureCredential acquired  ${elapsed()}`);

  const aiClient = new AzureOpenAI({
    azureADTokenProvider: tokenProvider,
    endpoint: AZURE_ENDPOINT,
    deployment: DEPLOYMENT,
    apiVersion: API_VERSION,
  });
  console.log(`  [auth]     AzureOpenAI client ready  ${elapsed()}`);

  // Separate lightweight client for gpt-4o-mini intent extraction
  const miniClient = new AzureOpenAI({
    azureADTokenProvider: tokenProvider,
    endpoint: AZURE_ENDPOINT,
    deployment: MINI_DEPLOYMENT,
    apiVersion: MINI_API_VERSION,
  });
  console.log(`  [auth]     gpt-4o-mini client ready  ${elapsed()}`);

  // ── 2. Launch browser ────────────────────────────────────────────────────
  banner("BROWSER — Launch & Navigate");
  const browser = await chromium.launch({ headless: true });
  console.log(`  [browser]  Chromium launched  ${elapsed()}`);

  const context = await browser.newContext({
    viewport: { width: DISPLAY_WIDTH, height: DISPLAY_HEIGHT },
  });
  try {
  const page = await context.newPage();
  console.log(`  [browser]  New context + page created  ${elapsed()}`);

  const navStart = Date.now();
  await page.goto(APP_URL, { waitUntil: "networkidle" });
  logTelemetry("navigation", {
    url:           APP_URL,
    title:         await page.title(),
    loadTime:      `${Date.now() - navStart}ms`,
    elapsed:       elapsed(),
  });

  // ── 3. Extract filter intent from natural language (gpt-4o-mini) ─────────
  banner("INTENT — Extract filter values from task");
  console.log(`  [intent]   task: "${USER_TASK}"`);
  const intentStart = Date.now();
  const intent = await extractFilters(miniClient, USER_TASK);
  console.log(`  [intent]   severity="${intent.severity || "(All)"}"  status="${intent.status || "(All)"}"  (${Date.now() - intentStart}ms)`);
  console.log(`  [intent]   reasoning: ${intent.reasoning}`);

  // ── 4. Pre-filter using Playwright (saves the AI ~10 steps & tokens) ────
  banner("PRE-FILTER — Set dropdowns via Playwright");
  await applyFilters(page, intent);

  // ── 5. Initial screenshot (already filtered) ─────────────────────────────
  banner("AGENT — Agentic Loop Starting");
  let screenshot = await takeScreenshot(page, "step-00-initial");

  // ── 6. Task description sent to the model (uses extracted filter values) ─
  const severityLabel = intent.severity || "All";
  const statusLabel   = intent.status   || "All";
  const task = `
You are controlling a web browser showing a customer complaints dashboard.
URL: ${APP_URL}

The page is ALREADY filtered — Severity=${severityLabel} and Status=${statusLabel} are set.
Do NOT touch the dropdowns. The user's original request was: "${USER_TASK}"

Your task:
1. Read ALL the complaint records currently visible on screen
2. Scroll down if there are more records below the fold
3. Return a concise text summary:
   - How many complaints total
   - Common themes or issues
   - Notable details (flight numbers, routes, dates, customer names if visible)

When done, respond with the summary text only — no more actions.
`.trim();

  // ── 7. Agentic loop ──────────────────────────────────────────────────────
  // `input` accumulates the full conversation: user messages, assistant actions,
  // computer_call_output (screenshots after each action).
  // This is how the model sees what happened after each action it took.
  let input: any[] = [
    {
      role: "user",
      content: [
        { type: "input_text", text: task },
        { type: "input_image", image_url: `data:image/png;base64,${screenshot}` },
      ],
    },
  ];

  let step = 0;
  let totalInputTokens = 0;
  let totalOutputTokens = 0;
  let totalActions = 0;
  let gotSummary = false;

  while (step < MAX_STEPS) {
    step++;
    const stepStart = Date.now();
    console.log(`\n┌─ Step ${step}/${MAX_STEPS}  ${elapsed()}`);
    console.log(`│  conversation turns in input: ${input.length}`);

    // Call the model (retry up to 5x on 429 rate-limit)
    let response: any;
    for (let attempt = 1; attempt <= 5; attempt++) {
      try {
        response = await aiClient.responses.create({
          model: DEPLOYMENT,
          tools: [
            {
              type: "computer_use_preview",
              display_width: DISPLAY_WIDTH,
              display_height: DISPLAY_HEIGHT,
              environment: "browser",
            } as any,
          ],
          input,
          truncation: "auto",
        } as any);
        break; // success
      } catch (err: any) {
        if (err?.status === 429 && attempt < 5) {
          const wait = attempt * 15000;
          console.log(`  [retry]    429 rate-limit — waiting ${wait / 1000}s (attempt ${attempt}/5)  ${elapsed()}`);
          await new Promise(r => setTimeout(r, wait));
        } else {
          throw err;
        }
      }
    }

    // Add the model's output to our conversation history
    input = [...input, ...(response as any).output];

    // ── Token usage telemetry ──
    const usage = (response as any).usage ?? {};
    const inputTok  = usage.input_tokens  ?? 0;
    const outputTok = usage.output_tokens ?? 0;
    totalInputTokens  += inputTok;
    totalOutputTokens += outputTok;

    // Separate computer_call actions from text responses
    const allOutput: any[] = (response as any).output ?? [];
    const computerCalls = allOutput.filter((b) => b.type === "computer_call");
    const textMessages = allOutput
      .filter((b) => b.type === "message")
      .flatMap((b) => b.content ?? [])
      .filter((c: any) => c.type === "output_text")
      .map((c: any) => c.text as string);

    console.log(`│  model response received in ${Date.now() - stepStart}ms`);
    logTelemetry("tokens (this step)", {
      input_tokens:    inputTok,
      output_tokens:   outputTok,
      total_so_far:    `${totalInputTokens} in / ${totalOutputTokens} out`,
    });
    console.log(`│  actions in this step: ${computerCalls.length}  |  text blocks: ${textMessages.length}`);

    // ── Model finished: it returned text with no more actions ──
    if (computerCalls.length === 0 && textMessages.length > 0) {
      const summary = textMessages.join("\n\n");

      await takeScreenshot(page, `step-${String(step).padStart(2, "0")}-final`);

      const summaryPath = path.join(OUT, "summary.txt");
      await fs.promises.writeFile(summaryPath, summary, "utf8");

      banner("FINAL SUMMARY");
      console.log(summary);

      banner("RUN TELEMETRY");
      logTelemetry("totals", {
        steps_taken:      step,
        total_actions:    totalActions,
        input_tokens:     totalInputTokens,
        output_tokens:    totalOutputTokens,
        total_tokens:     totalInputTokens + totalOutputTokens,
        wall_clock:       elapsed(),
        screenshots_dir:  OUT,
        summary_file:     summaryPath,
      });
      gotSummary = true;
      break;
    }

    // ── Execute each computer action and collect screenshots ──
    const callOutputs: any[] = [];

    for (const call of computerCalls) {
      await executeAction(page, call.action);
      totalActions++;
      await page.waitForTimeout(600); // let the page settle/animate
    }

    console.log(`└─ step ${step} done in ${Date.now() - stepStart}ms  total actions so far: ${totalActions}`);

    // One screenshot after all actions in this step
    screenshot = await takeScreenshot(page, `step-${String(step).padStart(2, "0")}`);

    // Build computer_call_output entries — one per call, all sharing the new screenshot
    for (const call of computerCalls) {
      callOutputs.push({
        type: "computer_call_output",
        call_id: call.call_id,
        output: {
          type: "input_image",
          image_url: `data:image/png;base64,${screenshot}`,
        },
      });
    }

    // Append the outputs to the conversation so the model sees what happened
    input = [...input, ...callOutputs];
  }

  if (step >= MAX_STEPS && !gotSummary) {
    banner("WARNING");
    console.log(`  Reached MAX_STEPS (${MAX_STEPS}) without a final summary.`);
    logTelemetry("partial totals", {
      steps_taken:   step,
      total_actions: totalActions,
      input_tokens:  totalInputTokens,
      output_tokens: totalOutputTokens,
      wall_clock:    elapsed(),
    });
  }

  } finally {
    await context.close();
    await browser.close();
    console.log(`\n[done] total wall-clock time: ${elapsed()}`);
  }
}

main().catch((err) => {
  console.error("Error:", err?.message ?? err);
  process.exit(1);
});
