import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GateRun, outcome } from "./GateRun";
import { Landing } from "./Landing";
import store from "./product.json";
import sample from "./sample.json";

const passing = sample.fixes[0].checks;

function noMeta() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the gate run", () => {
  it("walks the 12 checks once and stops at a person", () => {
    vi.useFakeTimers();
    const { container } = render(<GateRun checks={passing} />);
    expect(container.querySelectorAll(".gate-waiting")).toHaveLength(12);
    expect(container.querySelector(".gate-caption")).toHaveTextContent("Checking V01");
    // Each step is scheduled after the previous one renders, so time moves one step at a time.
    for (let step = 1; step <= 12; step += 1) {
      act(() => {
        vi.advanceTimersByTime(260);
      });
      expect(container.querySelectorAll(".gate-passed")).toHaveLength(step);
    }
    expect(container.querySelector(".gate-caption")).toHaveTextContent("All 12 checks passed. Waiting for a person to approve.");
  });

  it("shows the finished state at once when motion is reduced", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query }));
    const { container } = render(<GateRun checks={passing} />);
    expect(container.querySelectorAll(".gate-passed")).toHaveLength(12);
  });

  it("gives screen readers the result once, not twelve updates", () => {
    const { container } = render(<GateRun checks={passing} />);
    expect(container.querySelector(".gates")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".sr-only")).toHaveTextContent("All 12 checks passed");
  });

  it("names what stopped a fix, and treats a warning as a warning", () => {
    expect(outcome([{ rule_id: "V04", passed: false, severity: "block" }, { rule_id: "V10", passed: true, severity: "warn" }])).toBe(
      "Stopped by V04. Nobody can approve it.",
    );
    expect(outcome([{ rule_id: "V10", passed: false, severity: "warn" }])).toBe(
      "Passed with 1 warning(s). Waiting for a person to approve.",
    );
  });
});

describe("the landing page", () => {
  it("uses fixes the demo really produced, and says the template drafted them", () => {
    expect(sample.source).toMatch(/template script heuristic-v1, no LLM/);
    expect(sample.fixes.map((fix) => [fix.product_title, fix.status])).toEqual([
      ["Trailhead Daypack 20L", "pending_review"],
      ["Waxed Canvas Pencil Roll", "blocked"],
    ]);
    noMeta();
    const { container } = render(<Landing animate={false} />);
    expect(screen.getByText(/Drafted by the demo's template script, not an LLM/)).toBeInTheDocument();
    expect(container.querySelector(".stopped .gate-caption")).toHaveTextContent("Stopped by V04. Nobody can approve it.");
    expect(container.querySelector(".stopped .gate-stopped")).toHaveTextContent("V04");
  });

  it("still shows the figures copied from the eval reports, the failing rule included", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const text = container.textContent ?? "";
    for (const figure of ["323 of 329", "323 of 330", "Two of 102 fixes that should have passed", "57 of 57", "0 of 25", "10 of 30", "2 of 8", "40 of 40", "None was blocked"]) {
      expect(text).toContain(figure);
    }
    expect(text).toContain("PASS_WITH_KNOWN_FAILURES");
  });

  it("claims no production use and names no model vendor", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    expect(container.textContent).not.toMatch(/production|real store|claude|anthropic/i);
  });

  it("renders catalog text as text, never as markup", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    expect(container.querySelector(".fix-text p, .fix-text script")).toBeNull();
    expect(container.querySelector(".fix-text ins")).toHaveTextContent(/Trailhead Daypack 20L by Northline/);
  });

  it("mentions the shared sandbox only when the server says it is a public demo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ public_demo: true, demo_reset_minutes: 60 }), { status: 200 })),
    );
    render(<Landing animate={false} />);
    await waitFor(() => expect(screen.getByText(/resets every 60 minutes/)).toBeInTheDocument());
  });

  it("leaves the sandbox sentence out when the server cannot be asked", async () => {
    noMeta();
    render(<Landing animate={false} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/meta"));
    expect(screen.queryByText(/share the same sandbox/)).toBeNull();
  });
});

const ids = (prefix: string, count: number) =>
  Array.from({ length: count }, (_, index) => `${prefix}${String(index + 1).padStart(2, "0")}`);

describe("the product page", () => {
  it("walks one product through the seven steps, in order", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const steps = [...container.querySelectorAll("#how .lane > li")];
    expect(steps.map((step) => step.querySelector("h3")?.textContent)).toEqual([
      "Sync",
      "Audit",
      "Agent",
      "Twelve checks",
      "Judge",
      "A person",
      "Write-back",
    ]);
    expect(steps.filter((step) => step.querySelector("figure.snap"))).toHaveLength(7);
    // The lane is drawing only; screen readers get the numbered list and the snapshots.
    expect(container.querySelectorAll('#how .lane-line[aria-hidden="true"]')).toHaveLength(7);
  });

  it("shows the product as the simulated store holds it and as the demo run left it", () => {
    const [passed] = sample.fixes;
    // product.json is copied from fixtures/shopify/catalog_demo.json, and it must agree with the captured fix.
    expect(store.product.title).toBe(passed.product_title);
    expect(store.product.description_html).toBe(`<p>${passed.before.join(" ")}</p>`);
    expect(passed.findings.map((finding) => finding.rule_id)).toEqual(["A02"]);
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const lane = container.querySelector("#how .lane");
    expect(lane).toHaveTextContent(store.product.seo_description);
    expect(lane).toHaveTextContent("A02 Description is thin");
    expect(lane).toHaveTextContent(`Cites ${passed.citations[0].chunk_id}`);
    expect(lane).toHaveTextContent("The LLM judge did not run.");
    expect(lane).toHaveTextContent("Needs review");
    expect(container.querySelector("#how .stopped .gate-stopped")).toHaveTextContent("V04");
  });

  it("lists all 13 audit rules and all 12 checks", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const rules = [...container.querySelectorAll("#rules .rule-table tbody th")].map((cell) => cell.textContent);
    expect(rules).toEqual(ids("A", 13));
    const checks = [...container.querySelectorAll("#rules .check-id")].map((cell) => cell.textContent);
    expect(checks).toEqual(ids("V", 12));
  });

  it("keeps the pinned figures in the numbers section, the failing rule included", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const numbers = container.querySelector("#numbers")?.textContent ?? "";
    for (const figure of ["323 of 329", "323 of 330", "Two of 102 fixes that should have passed", "57 of 57", "0 of 25", "10 of 30", "2 of 8", "40 of 40", "None was blocked", "PASS_WITH_KNOWN_FAILURES"]) {
      expect(numbers).toContain(figure);
    }
    expect(container.querySelector("#numbers .number-failed")).toHaveTextContent("rule A13");
  });

  it("names no model vendor anywhere, in text or in attributes", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    expect(container.innerHTML).not.toMatch(/claude|anthropic/i);
  });

  it("links each masthead entry to a section of the page", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const targets = [...container.querySelectorAll("nav .section-nav a")].map((link) => link.getAttribute("href") ?? "");
    expect(targets).toEqual(["#how", "#rules", "#limits", "#numbers", "#inside", "#data", "#status"]);
    for (const target of targets) expect(container.querySelector(`section${target}`)).not.toBeNull();
  });

  it("draws the architecture as a labelled image coloured only by the stylesheet", () => {
    noMeta();
    const { container } = render(<Landing animate={false} />);
    const svg = container.querySelector(".arch svg");
    expect(svg).toHaveAttribute("role", "img");
    expect(svg?.getAttribute("aria-label")).toMatch(/12 validators/);
    // Colours come from CSS variables, so the drawing follows the dark theme.
    expect(svg?.querySelectorAll("[fill], [stroke], [style]")).toHaveLength(0);
  });
});
