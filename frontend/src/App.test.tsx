import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProposalDetail, ValidationRow } from "./api/client";
import { DecisionBar } from "./components/DecisionBar";
import { DiffPanel } from "./components/DiffPanel";
import { Gate } from "./components/Gate";
import { CHECK_IDS, GateStrip } from "./components/GateStrip";

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
