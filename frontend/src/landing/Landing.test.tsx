import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AUDIT_RULES, MOVES } from "./content";
import gate from "./gate.json";
import { FixText } from "./FixText";
import { CHECK_NAMES, outcome, type GateResult } from "./GateRun";
import { Landing } from "./Landing";
import store from "./product.json";
import rules from "./rules.json";
import sample from "./sample.json";
import { TryGate, resultKey } from "./TryGate";

const RESULTS: Record<string, GateResult> = gate.results;

function noMeta() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
}

// The whole page, with the gate's run already finished and no server behind it.
function page(): HTMLElement {
  noMeta();
  return render(<Landing animate={false} />).container;
}

function section(container: HTMLElement, id: string) {
  return within(container.querySelector<HTMLElement>(`#${id}`)!);
}

// The words on screen before the visitor opens or chooses anything. jsdom applies no stylesheet, so this takes out by
// hand what landing.css hides: panels that are not chosen, the bodies of closed details, the explanations behind the
// meter rows, and the gate's lines that are not added yet.
function atRest(container: HTMLElement): string {
  const main = container.querySelector("main")!.cloneNode(true) as HTMLElement;
  const hidden = "[hidden], details:not([open]) > :not(summary), .meter-more, .gate-line-empty";
  for (const element of main.querySelectorAll(hidden)) element.remove();
  return (main.textContent ?? "").replace(/\s+/g, " ");
}

const ids = (prefix: string, count: number) =>
  Array.from({ length: count }, (_, index) => `${prefix}${String(index + 1).padStart(2, "0")}`);

const idOf = (element: Element) => element.querySelector(".check-id, .tile-id")?.textContent;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the gate", () => {
  it("lights the 12 checks one by one, then tells screen readers the result once", () => {
    vi.useFakeTimers();
    noMeta();
    const { container } = render(<Landing />);
    const hero = container.querySelector<HTMLElement>("#gate")!;
    const status = within(hero).getByRole("status");
    expect(hero.querySelectorAll(".light-idle")).toHaveLength(12);
    // Each step is scheduled after the previous one renders, so time moves one step at a time.
    for (let step = 1; step <= 12; step += 1) {
      expect(status).toBeEmptyDOMElement();
      act(() => {
        vi.advanceTimersByTime(110);
      });
      expect(hero.querySelectorAll(".light-passed")).toHaveLength(step);
    }
    expect(status).toHaveTextContent("Clears all 12. Now a person decides.");
    // The bars are drawing only. The status region is the one live region on the whole page, and it spoke once.
    expect(hero.querySelector(".lights")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelectorAll('[role="status"], [aria-live]')).toHaveLength(1);

    // A choice starts a new run from the first check, and the region speaks again only when that run ends.
    fireEvent.click(within(hero).getByRole("button", { name: "add a link" }));
    expect(hero.querySelectorAll(".light-idle")).toHaveLength(12);
    for (let step = 1; step <= 12; step += 1) {
      expect(status).toBeEmptyDOMElement();
      act(() => {
        vi.advanceTimersByTime(110);
      });
    }
    expect(status).toHaveTextContent("Stopped by V04. Nobody can approve it.");
  });

  it("shows the finished state at once when motion is reduced", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query }));
    const { container } = render(<TryGate />);
    expect(container.querySelectorAll(".light-passed")).toHaveLength(12);
    expect(screen.getByRole("status")).toHaveTextContent("Clears all 12.");
    // A new fix is checked at once as well.
    fireEvent.click(screen.getByRole("button", { name: gate.additions[0].label }));
    expect(container.querySelectorAll(".light-idle")).toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent("Stopped by V04.");
  });

  it("names what stopped a fix, and treats a warning as a warning", () => {
    expect(outcome([{ rule_id: "V04", passed: false, severity: "block" }, { rule_id: "V10", passed: true, severity: "warn" }])).toBe(
      "Stopped by V04. Nobody can approve it.",
    );
    expect(outcome([{ rule_id: "V10", passed: false, severity: "warn" }])).toBe(
      "Clears with 1 warning. Now a person decides.",
    );
  });

  it("shows the recorded checks for a chosen addition, and names what stopped the fix", () => {
    const { container } = render(<TryGate animate={false} />);
    const [link] = gate.additions;
    const recorded = RESULTS[link.id];
    const toggle = screen.getByRole("button", { name: link.label });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(container.querySelectorAll(".gate-line-added")).toHaveLength(0);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    // The line joins the description, marked as added, with what the check found marked in it.
    const added = container.querySelector(".gate-line-added");
    expect(added).toHaveTextContent(`${link.line} added`);
    expect(added?.querySelector("mark")).toHaveTextContent(recorded.stopped_by[0].found);
    // The lights are the recorded answer, not something the page worked out.
    const stopped = [...container.querySelectorAll(".light")].map((light) => light.classList.contains("light-stopped"));
    expect(stopped).toEqual(recorded.checks.map((check) => !check.passed && check.severity !== "warn"));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Nobody can approve it.");
    for (const stop of recorded.stopped_by) {
      expect(status).toHaveTextContent(`${stop.rule_id} ${CHECK_NAMES[stop.rule_id]}. It found "${stop.found}".`);
    }

    fireEvent.click(toggle);
    expect(status).toHaveTextContent("Clears all 12. Now a person decides.");
    expect(container.querySelector("mark")).toBeNull();
  });

  it("has a recorded answer for every combination of additions", () => {
    const additions = gate.additions.map((addition) => addition.id);
    for (let mask = 0; mask < 2 ** additions.length; mask += 1) {
      const chosen = new Set(additions.filter((_, bit) => mask & (1 << bit)));
      const result = RESULTS[resultKey(chosen)];
      expect(result, `no answer for ${[...chosen].join(" and ") || "the plain draft"}`).toBeDefined();
      expect(result.checks.map((check) => check.rule_id)).toEqual(Object.keys(CHECK_NAMES));
      // What stopped the fix is exactly the blocking checks that failed.
      const failed = result.checks.filter((check) => !check.passed && check.severity !== "warn");
      expect(result.stopped_by.map((stop) => stop.rule_id)).toEqual(failed.map((check) => check.rule_id));
    }
    expect(Object.keys(RESULTS)).toHaveLength(2 ** additions.length);

    // The page reaches the last of them: every addition chosen, every stop named.
    render(<TryGate animate={false} />);
    for (const addition of [...gate.additions].reverse()) fireEvent.click(screen.getByRole("button", { name: addition.label }));
    expect(screen.getByRole("status").querySelectorAll("li")).toHaveLength(RESULTS[additions.join("+")].stopped_by.length);
  });
});

describe("the landing page", () => {
  it("uses fixes the demo really produced, and says the template drafted them", () => {
    expect(sample.source).toMatch(/template script heuristic-v1, no LLM/);
    expect(sample.fixes.map((fix) => [fix.product_title, fix.status])).toEqual([
      ["Trailhead Daypack 20L", "pending_review"],
      ["Waxed Canvas Pencil Roll", "blocked"],
    ]);
    // The gate's draft with the link added is the blocked fix the demo run captured, line for line and check for
    // check. So the caption may say that the demo adds that line itself.
    const [passed, blocked] = sample.fixes;
    expect(gate.product.title).toBe(blocked.product_title);
    expect([...gate.base, gate.additions[0].line]).toEqual(blocked.after);
    expect(RESULTS[gate.additions[0].id].checks).toEqual(blocked.checks);

    const container = page();
    expect(container.querySelector("#gate figcaption")).toHaveTextContent(
      "The demo's template script, not an LLM, drafted the first four lines and adds the link on purpose, to show a blocked fix. This page adds the other three.",
    );
    expect(container.querySelector("#how")).toHaveTextContent(`Drafted by ${passed.model}, a template script.`);
  });

  it("keeps the figures copied from the eval reports, the failing rule included", () => {
    const numbers = page().querySelector<HTMLElement>("#numbers")!;
    const pinned = [
      ["Known defects the block rules caught", "323 of 330", ""],
      ["Block rules, precision", "323 of 329", ""],
      ["Clean fixes wrongly blocked", "2 of 102", "Lower is better"],
      ["Invalid citations rejected", "57 of 57", ""],
      ["Valid citations wrongly rejected", "0 of 25", "Lower is better"],
      ["Injection phrases rule A13 found", "10 of 30", "Below its floor of 0.50. The report says PASS_WITH_KNOWN_FAILURES, not PASS."],
      ["Reworded questions found in the top three", "2 of 8", ""],
      [
        "Fixes reaching human review, one hosted-LLM run",
        "40 of 40",
        "Synthetic products, at about 0.025 USD each. None was blocked, so this run did not test the checks against a bad fix.",
      ],
    ];
    const rows = [...numbers.querySelectorAll(".meter")];
    const read = (row: Element, part: string) => row.querySelector(part)?.textContent;
    expect(rows.map((row) => [read(row, ".meter-title"), read(row, ".meter-figure"), read(row, ".meter-note") ?? ""])).toEqual(pinned);
    // Every bar is drawn at its own figure's share of the whole, on the one scale.
    for (const row of rows) {
      const [part, whole] = (read(row, ".meter-figure") ?? "").split(" of ").map(Number);
      const drawn = row.querySelector(".meter-fill")?.getAttribute("width") ?? "0.00%";
      expect(drawn).toBe(`${((part / whole) * 100).toFixed(2)}%`);
      expect(row.querySelector(".meter-svg")).toHaveAccessibleName(`${Math.round((part / whole) * 100)} percent`);
    }
    // The stopped colour is kept for the one measure below its floor, which also says so with an icon, in words and
    // with a tick at the floor.
    const failed = numbers.querySelectorAll(".meter-failed");
    expect(failed).toHaveLength(1);
    expect(failed[0]).toHaveTextContent("rule A13");
    expect(failed[0].querySelector(".meter-note")).toHaveTextContent("Below its floor of 0.50");
    expect(failed[0].querySelector(".meter-note .icon")).not.toBeNull();
    expect(failed[0].querySelector(".meter-floor")).toHaveAttribute("x1", "50%");
    for (const words of [
      "Lower is better",
      "PASS_WITH_KNOWN_FAILURES, not PASS",
      "All seven misses were V06.",
      "all six wrong stops were V07",
      "The embeddings are lexical, not semantic.",
      "at about 0.025 USD each",
      "None was blocked, so this run did not test the checks against a bad fix.",
    ]) {
      expect(numbers).toHaveTextContent(words);
    }
    // A tap on a name opens that row's explanation in place and closes the last one. The same words describe the
    // button, so they are also heard on focus.
    const [recall, precision] = within(numbers).getAllByRole("button");
    fireEvent.click(recall);
    expect(recall).toHaveAttribute("aria-expanded", "true");
    expect(recall).toHaveAccessibleDescription("All seven misses were V06.");
    fireEvent.click(precision);
    expect(recall).toHaveAttribute("aria-expanded", "false");
    expect(precision.closest(".meter")).toHaveClass("meter-open");
  });

  it("claims no production use and names no model vendor, in text or in attributes", () => {
    const container = page();
    expect(container.innerHTML).not.toMatch(/production|real store|claude|anthropic/i);
    const done = [...container.querySelectorAll(".status-done li")].map((item) => item.querySelector("summary, span")?.textContent);
    expect(done).toEqual([
      "API, job queue, write-back and webhooks run",
      "Rules and citation checker are measured",
      "A hosted LLM ran the agent once, on 40 synthetic products",
      "MCP server ran over streamable HTTP",
      "Embedded mode is built and tested",
    ]);
    // The lede is the page's own account of itself, so it is pinned whole: a sentence added to it is a new claim.
    expect(container.querySelector(".lede")?.textContent?.replace(/\s+/g, " ").trim()).toBe(
      "Catalog Gatekeeper audits a Shopify-style catalog. A hosted LLM is meant to propose fixes; in the demo, a " +
        "template script drafts them. Every fix must clear deterministic checks, then a person approves the exact " +
        "change. It is a personal project, built on synthetic data.",
    );
  });

  it("keeps what is simulated and what has not run in view, without opening anything", () => {
    const text = atRest(page());
    for (const fact of [
      "Simulated store",
      "in the demo, a template script drafts them",
      "The demo's template script, not an LLM, drafted the first four lines",
      "The rules, the synthetic data and the tests share one author",
      "They do not show how the rules behave on a merchant's catalog.",
      "A hosted LLM ran the agent once, on 40 synthetic products",
      "it has never run against a merchant's store",
      "Embedded mode has not yet run against Shopify itself",
      "It is a personal project, built on synthetic data.",
      "The report says PASS_WITH_KNOWN_FAILURES, not PASS.",
      "None was blocked, so this run did not test the checks against a bad fix.",
      "Not in the Shopify App Store",
      "Live adapter has never called a store",
      "Rules are English word lists",
      "Eval data is synthetic; its author wrote the rules",
      "Repair quality has no automatic score",
      "Reviewer tokens never expire",
      "Rate limits only in the public demo",
    ]) {
      expect(text).toContain(fact);
    }
    expect(document.querySelector("#gate .gate-head")).toHaveTextContent("Simulated store");
  });

  it("renders catalog text as text, never as markup", () => {
    const container = page();
    expect(container.querySelector(".fix-text p, .fix-text script")).toBeNull();
    expect(container.querySelector(".fix-text ins")).toHaveTextContent(/Trailhead Daypack 20L by Northline/);
    // A line in the gate holds its text, the mark around what a check found and the word "added": nothing else.
    for (const addition of gate.additions) fireEvent.click(screen.getByRole("button", { name: addition.label }));
    expect(container.querySelectorAll(".gate-line mark")).toHaveLength(gate.additions.length);
    expect(container.querySelectorAll(".gate-line *:not(ins):not(mark):not(.tag)")).toHaveLength(0);
    // The store's broken HTML is shown as the characters it is.
    const looksFor = section(container, "rules");
    fireEvent.click(looksFor.getByRole("tab", { name: /A04/ }));
    expect(looksFor.getByRole("tabpanel")).toHaveTextContent("<ul> closed by </p>");
    expect(looksFor.getByRole("tabpanel").querySelector("ul")).toBeNull();
    const hostile = { ...sample.fixes[0], after: ['<img src="x"><b>bold</b>'] };
    const drawn = render(<FixText fix={hostile} />).container;
    expect(drawn.querySelector("img, b")).toBeNull();
    expect(drawn).toHaveTextContent('<img src="x"><b>bold</b>');
  });

  it("mentions the shared sandbox only when the server says it is a public demo", async () => {
    const answer = (body: object, status: number) =>
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
    answer({ public_demo: true, demo_reset_minutes: 60 }, 200);
    const told = render(<Landing animate={false} />);
    await waitFor(() => expect(screen.getByText(/resets every 60 minutes/)).toBeInTheDocument());
    told.unmount();

    // Left out when the server cannot be asked, and when it answers but is not a public demo.
    for (const [body, status] of [
      [{}, 503],
      [{ public_demo: false, demo_reset_minutes: 60 }, 200],
    ] as const) {
      answer(body, status);
      const silent = render(<Landing animate={false} />);
      await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/meta"));
      expect(screen.queryByText(/share the same sandbox/)).toBeNull();
      silent.unmount();
    }
  });

  it("states each hard limit, with its caveat one tap away", () => {
    const limits = page().querySelector<HTMLElement>("#limits")!;
    const cards = [...limits.querySelectorAll(".limit-cards summary")].map((summary) => summary.textContent);
    expect(cards).toEqual([
      "The agent cannot touch another product.",
      "Nobody can approve a stopped fix.",
      "The judge cannot clear a check or block a fix.",
      "An approval covers only the exact change on screen.",
      "Write-back will not overwrite a newer edit by the merchant.",
      "No new write starts while write-back is switched off.",
      "The template script cannot write to a merchant's store.",
      "The app cannot rewrite its audit log.",
      "The MCP server cannot change anything.",
    ]);
    for (const caveat of [
      "no reviewer can approve it, admins included",
      "On a live Shopify store a gap of about one round trip remains.",
      "A write already on its way to the store is not called back.",
      "The public demo's reset empties it",
    ]) {
      expect(limits).toHaveTextContent(caveat);
    }
  });
});

describe("the product page", () => {
  it("walks one product through the seven steps, in order", () => {
    const [passed] = sample.fixes;
    // product.json is copied from fixtures/shopify/catalog_demo.json, and it must agree with the captured fix.
    expect(store.product.title).toBe(passed.product_title);
    expect(store.product.description_html).toBe(`<p>${passed.before.join(" ")}</p>`);
    expect(passed.findings.map((finding) => finding.rule_id)).toEqual(["A02"]);

    const container = page();
    const how = section(container, "how");
    const steps = how.getAllByRole("tab");
    expect(steps.map((step) => step.textContent)).toEqual([
      "Sync",
      "Audit",
      "Agent",
      "Twelve checks",
      "Judge",
      "A person",
      "Write-back",
    ]);
    expect(container.querySelectorAll("#how .step-caption")).toHaveLength(7);
    for (const caption of container.querySelectorAll("#how .step-caption")) {
      expect((caption.textContent ?? "").trim().split(/\s+/).length).toBeLessThanOrEqual(25);
    }

    // What each step shows of the product: as the simulated store holds it, then as the demo run left it.
    const shows = [
      store.product.seo_description,
      "A02 Thin description",
      `Cites ${passed.citations[0].chunk_id}`,
      "Clears all 12. Now a person decides.",
      "The LLM judge did not run.",
      "Needs review",
      "these are its possible endings, not its result",
    ];
    const onScreen = (step: number) => {
      expect(steps[step]).toHaveAttribute("aria-selected", "true");
      // One panel at a time: the query skips the hidden ones and would fail on two.
      expect(how.getByRole("tabpanel")).toHaveTextContent(shows[step]);
    };
    onScreen(0);
    // The Next button, the arrow keys and a step itself all move along the lane.
    fireEvent.click(how.getByRole("button", { name: "Next step" }));
    onScreen(1);
    fireEvent.keyDown(steps[1], { key: "ArrowRight" });
    expect(steps[2]).toHaveFocus();
    onScreen(2);
    for (let step = 3; step < steps.length; step += 1) {
      fireEvent.click(steps[step]);
      onScreen(step);
    }
    expect(how.getByRole("button", { name: "Next step" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(how.getByRole("button", { name: "Previous step" }));
    onScreen(5);
    fireEvent.keyDown(steps[5], { key: "ArrowLeft" });
    onScreen(4);
    fireEvent.keyDown(steps[4], { key: "Home" });
    onScreen(0);
    expect(how.getByRole("button", { name: "Previous step" })).toHaveAttribute("aria-disabled", "true");
  });

  it("lists all 13 audit rules and all 12 checks, and shows a rule's example from the simulated store", () => {
    const container = page();
    const looksFor = section(container, "rules");
    const tiles = looksFor.getAllByRole("tab");
    expect(tiles.map(idOf)).toEqual(ids("A", 13));
    // The agent may draft fixes for A01 to A08 and the other five are a person's: the outline shows it and the key
    // says it.
    expect(tiles.filter((tile) => tile.classList.contains("tile-agent")).map(idOf)).toEqual(ids("A", 8));
    expect(tiles.filter((tile) => tile.classList.contains("tile-person"))).toHaveLength(5);
    expect(tiles[0]).toHaveAccessibleDescription("The agent may draft a fix");
    expect(tiles[12]).toHaveAccessibleDescription("Left to a person");

    // A tile's panel shows what running that rule over the simulated store found. One is open at first, so the tiles
    // read as something to press, and choosing another swaps it.
    const examples: Record<string, { product: string; found: string }> = rules.examples;
    expect(Object.keys(examples)).toEqual(AUDIT_RULES.map((rule) => rule.id));
    expect(looksFor.getByRole("tabpanel")).toHaveTextContent(
      `In the simulated store: ${examples.A03.product}. ${examples.A03.found}`,
    );
    fireEvent.click(tiles[12]);
    const panel = looksFor.getByRole("tabpanel");
    expect(panel).toHaveTextContent(`In the simulated store: ${examples.A13.product}. ${examples.A13.found}`);
    expect(panel).toHaveTextContent("High severity");

    const checks = [...container.querySelectorAll("#rules .check-list li")];
    expect(checks.map(idOf)).toEqual(ids("V", 12));
    expect(checks.filter((check) => check.textContent?.includes("warns only")).map(idOf)).toEqual(["V10"]);
    // The four checks the gate can trip link back to it.
    expect(checks.filter((check) => check.querySelector('a[href="#gate"]')).map(idOf)).toEqual(["V04", "V05", "V07", "V11"]);
    expect(container.querySelector("#gate")).not.toBeNull();
  });

  it("shows where a fix can go from a chosen state", () => {
    const container = page();
    const limits = section(container, "limits");
    const states = limits.getAllByRole("tab");
    const names = states.map((state) => state.getAttribute("aria-label"));
    // Every move in the list is drawn, between two boxes that exist, and is in the sentence that says it.
    const edges = [...container.querySelectorAll("#limits .move-edge")];
    expect(edges).toHaveLength(MOVES.flatMap((move) => move.next).length);
    expect(edges.every((edge) => edge.getAttribute("d"))).toBe(true);
    for (const move of MOVES) {
      expect(names).toContain(move.from);
      for (const to of move.next) {
        expect(names).toContain(to);
        expect(move.to).toContain(to);
      }
    }

    // Needs review is chosen at first, so its moves are already marked.
    expect(limits.getByRole("tabpanel")).toHaveTextContent("From Needs review, a fix can become");
    const writing = MOVES[3];
    fireEvent.click(states[names.indexOf(writing.from)]);
    expect(limits.getByRole("tabpanel")).toHaveTextContent(`From Writing, a fix can become ${writing.to}.`);
    const marked = [...container.querySelectorAll("#limits .move-node-next")].map((node) => node.getAttribute("aria-label"));
    expect(marked.sort()).toEqual([...writing.next].sort());
    expect(container.querySelectorAll("#limits .move-edge-on")).toHaveLength(writing.next.length);

    fireEvent.click(states[names.indexOf("Blocked")]);
    expect(limits.getByRole("tabpanel")).toHaveTextContent("From Blocked, a fix cannot move again.");
    expect(container.querySelectorAll("#limits .move-node-next")).toHaveLength(0);
    expect(container.querySelector("#limits figcaption")).toHaveTextContent("A database trigger enforces these moves");
  });

  it("links each masthead entry to a section of the page", () => {
    const container = page();
    const targets = [...container.querySelectorAll("nav .section-nav a")].map((link) => link.getAttribute("href") ?? "");
    expect(targets).toEqual(["#how", "#rules", "#limits", "#numbers", "#inside", "#status", "#data"]);
    for (const target of targets) expect(container.querySelector(`section${target}`)).not.toBeNull();
  });

  it("draws the architecture as a labelled drawing coloured only by the stylesheet", () => {
    const container = page();
    const svg = container.querySelector(".arch svg");
    expect(svg).toHaveAccessibleName("Architecture");
    expect(svg).toHaveAccessibleDescription(/12 validators, which block or warn/);
    // Colours come from CSS variables, so every drawing on the page follows the dark theme.
    expect(container.querySelectorAll("svg[fill], svg[stroke], svg [fill], svg [stroke], [style]")).toHaveLength(0);

    // Pointing at a box, focusing it or tapping it says what that part does.
    const inside = section(container, "inside");
    expect(inside.queryByRole("tabpanel")).toBeNull();
    fireEvent.mouseEnter(inside.getByRole("tab", { name: "Judge" }));
    expect(inside.getByRole("tabpanel")).toHaveTextContent("Judge. It can only escalate.");
    fireEvent.focus(inside.getByRole("tab", { name: "Shopify store. Simulated in the demo" }));
    expect(inside.getByRole("tabpanel")).toHaveTextContent("Its webhook is only a signal that something changed.");
  });
});
