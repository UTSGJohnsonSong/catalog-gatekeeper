import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProposalDetail, ValidationRow } from "./api/client";
import { MemoryRouter } from "react-router-dom";
import { App, ModeBanner } from "./App";
import { DecisionBar } from "./components/DecisionBar";
import { DiffPanel } from "./components/DiffPanel";
import { Gate } from "./components/Gate";
import { CHECK_IDS, GateStrip } from "./components/GateStrip";
import { LoginPage } from "./pages/Other";

const row = (rule_id: string, passed = true, severity = "block"): ValidationRow => ({
  rule_id,
  name: rule_id,
  kind: "validator",
  severity,
  passed,
  evidence: {},
});

function proposal(overrides: Partial<ProposalDetail> = {}): ProposalDetail {
  return {
    id: "p1",
    status: "pending_review",
    risk_score: 0,
    risk_reasons: [],
    rationale: "",
    product_id: 1,
    product_title: "Bottle",
    product_gid: "g",
    base_content_hash: "h",
    diffs: [],
    validation: CHECK_IDS.map((id) => row(id)),
    citations: [],
    findings: [],
    review_hash: "a".repeat(64),
    model_mode: "demo",
    model: "heuristic-v1",
    provider: "heuristic",
    run_flags: [],
    created_at: "2026-10-31T00:00:00Z",
    decided_at: null,
    applied_at: null,
    writeback_status: null,
    writeback_errors: [],
    judge_discarded: 0,
    judge_status: "disabled",
    ...overrides,
  };
}

const bar = (p: ProposalDetail, handlers: { onApprove?: () => void; onReject?: () => void; error?: string | null } = {}) =>
  render(
    <DecisionBar
      proposal={p}
      busy={false}
      error={handlers.error ?? null}
      onApprove={handlers.onApprove ?? vi.fn()}
      onReject={handlers.onReject ?? vi.fn()}
    />,
  );

describe("DecisionBar", () => {
  it("offers no approve button on a blocked proposal and names what stopped it", () => {
    bar(proposal({ status: "blocked", validation: [row("V05", false), row("V04", false), row("V10", false, "warn")] }));
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.getByText(/Stopped by V05, V04\./)).toBeInTheDocument();
  });

  it.each(["applied", "rejected", "stale", "failed", "approved", "applying"])("offers no decision once %s", (status) => {
    bar(proposal({ status }));
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
  });

  it("approves a low-risk proposal without a typed confirmation", () => {
    const onApprove = vi.fn();
    bar(proposal(), { onApprove });
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(onApprove).toHaveBeenCalledWith(false);
  });

  it("requires typing APPROVE on a high-risk proposal", () => {
    const onApprove = vi.fn();
    bar(proposal({ risk_score: 60 }), { onApprove });
    const button = screen.getByRole("button", { name: /approve/i });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type APPROVE/), { target: { value: "approve" } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type APPROVE/), { target: { value: "APPROVE" } });
    fireEvent.click(button);
    expect(onApprove).toHaveBeenCalledWith(true);
  });

  it("needs a reason to reject", () => {
    const onReject = vi.fn();
    bar(proposal(), { onReject });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const submit = screen.getByRole("button", { name: "Reject this fix" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Why are you rejecting/), { target: { value: "wrong tone" } });
    fireEvent.click(submit);
    expect(onReject).toHaveBeenCalledWith("wrong tone");
  });

  it("shows a conflict message when one arrives", () => {
    bar(proposal(), { error: "proposal changed since you loaded it" });
    expect(screen.getByRole("alert")).toHaveTextContent("proposal changed");
  });
});

describe("DiffPanel", () => {
  it("shows added and removed words", () => {
    const { container } = render(<DiffPanel diffs={[{ field: "seo_title", before: "Old name", after: "New name" }]} />);
    expect(container.querySelector("ins")).toHaveTextContent("New");
    expect(container.querySelector("del")).toHaveTextContent("Old");
  });

  it("renders catalog HTML as text, never as markup", () => {
    const { container } = render(
      <DiffPanel diffs={[{ field: "description_html", before: "", after: "<script>alert(1)</script><img src=x onerror=y>" }]} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("diff-description_html").textContent).toContain("<script>alert(1)</script>");
  });
});

describe("the gate", () => {
  it("draws 12 ticks in a fixed order and labels what stopped the fix", () => {
    const { container } = render(<GateStrip failed={["V05"]} />);
    const ticks = [...container.querySelectorAll(".tick")];
    expect(ticks).toHaveLength(12);
    expect(ticks[4]).toHaveClass("tick-stopped");
    expect(ticks[0]).toHaveClass("tick-passed");
    expect(screen.getByRole("img")).toHaveAccessibleName("Stopped by V05");
  });

  it("treats V10 as a warning, not a stop", () => {
    const { container } = render(<GateStrip failed={["V10"]} />);
    expect(container.querySelectorAll(".tick")[9]).toHaveClass("tick-warned");
    expect(screen.getByRole("img")).toHaveAccessibleName("All blocking checks passed");
  });

  it("opens the evidence of a failed check and lists judge rows after the 12 checks", () => {
    const rows = [
      ...CHECK_IDS.map((id) => (id === "V04" ? { ...row(id, false), evidence: { new: ["www.bad.example"] } } : row(id))),
      { ...row("J01", false, "warn"), kind: "judge", evidence: { concerns: [] } },
    ];
    render(<Gate rows={rows} />);
    expect(screen.getByText(/www\.bad\.example/)).toBeVisible();
    expect(screen.getByText(/claim the source does not support/)).toBeInTheDocument();
  });
});

describe("honest labels", () => {
  const banner = (meta: object | null) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        meta ? new Response(JSON.stringify(meta), { status: 200 }) : new Response("{}", { status: 500 }),
      ),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <ModeBanner />
      </QueryClientProvider>,
    );
  };
  const demo = {
    demo_mode: true,
    model_mode: "demo",
    model: "heuristic-v1",
    provider: "heuristic",
    shopify_adapter: "fixture",
    judge: "disabled",
    writeback_env_enabled: true,
    writeback_shop_enabled: true,
  };

  it("says the model is a template and the store is simulated", async () => {
    banner(demo);
    await waitFor(() => expect(screen.getByText(/template script, not an LLM/)).toBeInTheDocument());
    expect(screen.getByText(/store is simulated/)).toBeInTheDocument();
  });

  it("says when writing is off", async () => {
    banner({ ...demo, writeback_shop_enabled: false });
    await waitFor(() => expect(screen.getByText(/Writing to the store is switched off/)).toBeInTheDocument());
  });

  it("does not go quiet when the mode cannot be loaded", async () => {
    banner(null);
    await waitFor(() => expect(screen.getByText(/Could not confirm which model/)).toBeInTheDocument());
  });

  it("warns that a public demo is shared and resets", async () => {
    banner({ ...demo, public_demo: true, demo_token: "demo-reviewer", demo_reset_minutes: 60 });
    await waitFor(() => expect(screen.getByText(/other visitors see what you do/)).toBeInTheDocument());
    expect(screen.getByText(/resets every 60 minutes/)).toBeInTheDocument();
  });
});

describe("sign-in wiring", () => {
  it("fills in the token the server names, once the server has said it", async () => {
    const meta = {
      demo_mode: true,
      public_demo: true,
      demo_token: "demo-reviewer",
      demo_reset_minutes: 60,
      model_mode: "demo",
      model: "heuristic-v1",
      provider: "heuristic",
      shopify_adapter: "fixture",
      judge: "disabled",
      writeback_env_enabled: true,
      writeback_shop_enabled: true,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("/api/meta") ? new Response(JSON.stringify(meta), { status: 200 }) : new Response("{}", { status: 401 }),
      ),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByLabelText("Reviewer token")).toHaveValue("demo-reviewer"));
  });
});

describe("sign-in", () => {
  it("fills in the token the server offers, and nothing when it offers none", () => {
    const { unmount } = render(<LoginPage demoToken="demo-reviewer" onDone={() => undefined} />);
    expect(screen.getByLabelText("Reviewer token")).toHaveValue("demo-reviewer");
    expect(screen.getByText(/accepts the filled-in token/)).toBeInTheDocument();
    unmount();
    render(<LoginPage demoToken={null} onDone={() => undefined} />);
    expect(screen.getByLabelText("Reviewer token")).toHaveValue("");
    expect(screen.queryByText(/accepts the filled-in token/)).not.toBeInTheDocument();
  });
});

describe("failures stay visible", () => {
  it("shows a conflict even after the status moved on", () => {
    bar(proposal({ status: "approved" }), { error: "proposal is approved" });
    expect(screen.getByRole("alert")).toHaveTextContent("proposal is approved");
  });

  it("says a failed write may have reached the store, and why", () => {
    bar(proposal({ status: "failed", writeback_errors: [{ message: "outcome_unknown: an earlier write may have reached the store" }] }));
    expect(screen.getByText(/may or may not hold this text/)).toBeInTheDocument();
    expect(screen.getByText(/outcome_unknown/)).toBeInTheDocument();
  });

  it("says approving will wait when writing is off", () => {
    render(<DecisionBar proposal={proposal()} busy={false} error={null} writesEnabled={false} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByRole("button", { name: /will wait/ })).toBeInTheDocument();
  });
});
