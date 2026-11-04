import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GateRun, outcome } from "./GateRun";
import { Landing } from "./Landing";
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
