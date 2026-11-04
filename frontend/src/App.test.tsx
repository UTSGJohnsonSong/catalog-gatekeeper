import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { getToken, setToken, type ProposalDetail, type ValidationRow } from "./api/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App, ComponentsMissing, ModeBanner } from "./App";
import { DecisionBar } from "./components/DecisionBar";
import { DiffPanel } from "./components/DiffPanel";
import { Gate } from "./components/Gate";
import { CHECK_IDS, GateStrip } from "./components/GateStrip";
import { AppLink, StatusBadge } from "./components/Page";
import { LoginPage } from "./pages/Other";
import { QueuePage } from "./pages/Queue";

// jsdom never loads Shopify's CDN script, so Polaris elements stay plain custom-element hosts with no roles. These
// helpers find them by tag and text, and drive them the way the real components report input: a value on the element
// and an input event.
function polaris(tag: string, text: string | RegExp): HTMLElement | null {
  const found = [...document.querySelectorAll<HTMLElement>(tag)].filter((element) => {
    const content = element.textContent?.trim() ?? "";
    return typeof text === "string" ? content === text : text.test(content);
  });
  return found[0] ?? null;
}
const button = (text: string | RegExp) => polaris("s-button", text);
const field = (label: string) =>
  [...document.querySelectorAll<HTMLElement>("s-text-field, s-text-area")].find((element) =>
    element.getAttribute("label")?.startsWith(label),
  ) ?? null;
const fieldValue = (element: HTMLElement | null) =>
  (element as (HTMLElement & { value?: string }) | null)?.value ?? element?.getAttribute("value") ?? null;
function typeInto(element: HTMLElement | null, value: string) {
  if (!element) throw new Error("no such field");
  (element as HTMLElement & { value: string }).value = value;
  fireEvent.input(element);
}
// A screen-reader announcer repeats some sentences, so banners are found by tag, not by unique text.
const bannerWith = (text: RegExp) =>
  [...document.querySelectorAll<HTMLElement>("s-banner")].find((banner) => text.test(banner.textContent ?? "")) ?? null;

type Route = Record<string, (calls: number) => Response>;
// A fetch stub that answers by path and counts calls per path.
function serve(routes: Route) {
  const calls: Record<string, number> = {};
  const stub = vi.fn(async (url: string) => {
    const path = Object.keys(routes).find((prefix) => String(url).startsWith(prefix));
    if (!path) return new Response("{}", { status: 404 });
    calls[path] = (calls[path] ?? 0) + 1;
    return routes[path](calls[path]);
  });
  vi.stubGlobal("fetch", stub);
  return stub;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const queueItem = (id: string, status: string, title: string) => ({
  id,
  status,
  title,
  risk_score: 0,
  failed_rules: [],
  created_at: "2026-11-04T00:00:00Z",
});

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
    expect(button(/approve/i)).toBeNull();
    expect(bannerWith(/Stopped by V05, V04\./)).toHaveAttribute("tone", "critical");
  });

  it.each(["applied", "rejected", "stale", "failed", "approved", "applying"])("offers no decision once %s", (status) => {
    bar(proposal({ status }));
    expect(button(/approve/i)).toBeNull();
    expect(button(/reject/i)).toBeNull();
  });

  it("approves a low-risk proposal without a typed confirmation", () => {
    const onApprove = vi.fn();
    bar(proposal(), { onApprove });
    expect(field("Risk is")).toBeNull();
    fireEvent.click(button(/approve/i)!);
    expect(onApprove).toHaveBeenCalledWith(false);
  });

  it("requires typing APPROVE on a high-risk proposal", () => {
    const onApprove = vi.fn();
    bar(proposal({ risk_score: 60 }), { onApprove });
    expect(button(/approve/i)).toHaveAttribute("disabled");
    typeInto(field("Risk is 60. Type APPROVE"), "approve");
    expect(button(/approve/i)).toHaveAttribute("disabled");
    typeInto(field("Risk is 60. Type APPROVE"), "APPROVE");
    expect(button(/approve/i)).not.toHaveAttribute("disabled");
    fireEvent.click(button(/approve/i)!);
    expect(onApprove).toHaveBeenCalledWith(true);
  });

  it("needs a reason to reject", () => {
    const onReject = vi.fn();
    bar(proposal(), { onReject });
    expect(field("Why are you rejecting")).toBeNull();
    fireEvent.click(button("Reject")!);
    expect(button("Reject this fix")).toHaveAttribute("disabled");
    typeInto(field("Why are you rejecting"), "bad");
    expect(button("Reject this fix")).toHaveAttribute("disabled");
    typeInto(field("Why are you rejecting"), "wrong tone");
    fireEvent.click(button("Reject this fix")!);
    expect(onReject).toHaveBeenCalledWith("wrong tone");
  });

  it("shows a conflict message when one arrives", () => {
    bar(proposal(), { error: "proposal changed since you loaded it" });
    expect(bannerWith(/proposal changed/)).toHaveAttribute("tone", "critical");
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
    await waitFor(() => expect(fieldValue(field("Reviewer token"))).toBe("demo-reviewer"));
  });
});

describe("sign-in", () => {
  it("fills in the token the server offers, and nothing when it offers none", () => {
    const login = (demoToken: string | null) =>
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <LoginPage demoToken={demoToken} onDone={() => undefined} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    const { unmount } = login("demo-reviewer");
    expect(fieldValue(field("Reviewer token"))).toBe("demo-reviewer");
    expect(screen.getByText(/accepts the filled-in token/)).toBeInTheDocument();
    unmount();
    login(null);
    expect(fieldValue(field("Reviewer token"))).toBe("");
    expect(screen.queryByText(/accepts the filled-in token/)).not.toBeInTheDocument();
    expect(button("Sign in")).toHaveAttribute("disabled");
  });

  const loginWith = (onDone: () => void) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <LoginPage demoToken="demo-reviewer" onDone={onDone} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it("signs in when Enter is pressed in the token field", async () => {
    serve({ "/api/whoami": () => json({ name: "demo_reviewer", role: "reviewer" }), "/api/meta": () => json({}) });
    const onDone = vi.fn();
    loginWith(onDone);
    fireEvent.keyDown(field("Reviewer token")!, { key: "Enter" });
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(getToken()).toBe("demo-reviewer");
    setToken(null);
  });

  it("says when a token is refused, and forgets it", async () => {
    serve({ "/api/whoami": () => json({ detail: "no" }, 401), "/api/meta": () => json({}) });
    const onDone = vi.fn();
    const { container } = loginWith(onDone);
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(bannerWith(/That token was not accepted/)).toHaveAttribute("tone", "critical"));
    expect(onDone).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it("lets the next sign-in stay signed in after a token expired mid-session", async () => {
    const meta = { demo_mode: true, demo_token: "demo-reviewer", model_mode: "demo", shopify_adapter: "fixture", writeback_env_enabled: true, writeback_shop_enabled: true };
    // The token works, then expires; the token signed in with afterwards works again. The order matters: the
    // query cache keeps an old error next to old data, which is what used to sign the next sign-in straight out.
    serve({
      "/api/meta": () => json(meta),
      "/api/whoami": (calls) => (calls === 2 ? json({ detail: "expired" }, 401) : json({ name: "demo_reviewer", role: "reviewer" })),
      "/api/queue": () => json({ items: [], counts: {} }),
      "/api/ops": () => json({}),
    });
    setToken("soon-expired-token");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(document.querySelector('s-page[heading="Review queue"]')).not.toBeNull());
    await waitFor(() => expect(client.getQueryData(["whoami"])).toBeTruthy());
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["whoami"] });
    });
    await waitFor(() => expect(field("Reviewer token")).not.toBeNull());
    await waitFor(() => expect(fieldValue(field("Reviewer token"))).toBe("demo-reviewer"));
    fireEvent.keyDown(field("Reviewer token")!, { key: "Enter" });
    await waitFor(() => expect(document.querySelector('s-page[heading="Review queue"]')).not.toBeNull());
    // Give a stale 401 the chance to sign the reviewer out again.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(document.querySelector('s-page[heading="Review queue"]')).not.toBeNull();
    expect(field("Reviewer token")).toBeNull();
    setToken(null);
  });
});

describe("the queue", () => {
  const queue = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <QueuePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it("filters by status, with counts from the server rather than the capped list", async () => {
    serve({
      "/api/queue": () =>
        json({
          items: [queueItem("a", "pending_review", "Tent"), queueItem("b", "blocked", "Mug"), queueItem("c", "applied", "Tee")],
          counts: { pending_review: 30, blocked: 2, applied: 7 },
        }),
      "/api/whoami": () => json({ name: "demo_reviewer", role: "reviewer" }),
      "/api/ops": () => json({}),
      "/api/meta": () => json({}),
    });
    queue();
    await waitFor(() => expect(button("Needs review (30)")).not.toBeNull());
    expect(button("All (39)")).not.toBeNull();
    expect(polaris("s-link", "Tent")).not.toBeNull();
    expect(polaris("s-link", "Mug")).toBeNull();
    expect(polaris("s-button", "Audit more products")).toBeNull();
    fireEvent.click(button("Blocked (2)")!);
    expect(polaris("s-link", "Mug")).not.toBeNull();
    expect(polaris("s-link", "Tent")).toBeNull();
    expect(button("Blocked (2)")).toHaveAttribute("variant", "secondary");
  });

  it("shows no counts when the queue could not be loaded", async () => {
    serve({
      "/api/queue": () => json({ detail: "down" }, 500),
      "/api/whoami": () => json({ name: "demo_reviewer", role: "reviewer" }),
      "/api/ops": () => json({}),
      "/api/meta": () => json({}),
    });
    queue();
    await waitFor(() => expect(bannerWith(/Could not load the queue/)).not.toBeNull());
    expect(button("Needs review")).not.toBeNull();
    expect(button(/\(0\)/)).toBeNull();
  });
});

describe("failures stay visible", () => {
  it("shows a conflict even after the status moved on", () => {
    bar(proposal({ status: "approved" }), { error: "proposal is approved" });
    expect(bannerWith(/proposal is approved/)).toHaveAttribute("tone", "critical");
  });

  it("says a failed write may have reached the store, and why", () => {
    bar(proposal({ status: "failed", writeback_errors: [{ message: "outcome_unknown: an earlier write may have reached the store" }] }));
    expect(polaris("s-paragraph", /may or may not hold this text/)).toBeInTheDocument();
    expect(screen.getByText(/outcome_unknown/)).toBeInTheDocument();
  });

  it("keeps one live region across statuses, so a decision's outcome is announced", () => {
    const { rerender } = bar(proposal());
    const region = document.querySelector('[aria-live="polite"]');
    expect(region).toHaveTextContent("");
    rerender(<DecisionBar proposal={proposal({ status: "applied" })} busy={false} error={null} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(document.querySelector('[aria-live="polite"]')).toBe(region);
    expect(region).toHaveTextContent("Written to the store and verified.");
  });

  it("ignores a click on a disabled approve or reject, whatever Polaris does", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    bar(proposal({ risk_score: 60 }), { onApprove, onReject });
    fireEvent.click(button(/approve/i)!);
    expect(onApprove).not.toHaveBeenCalled();
    fireEvent.click(button("Reject")!);
    typeInto(field("Why are you rejecting"), "bad");
    fireEvent.click(button("Reject this fix")!);
    expect(onReject).not.toHaveBeenCalled();
  });

  it("says approving will wait when writing is off", () => {
    render(<DecisionBar proposal={proposal()} busy={false} error={null} writesEnabled={false} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(button(/will wait/)).toBeInTheDocument();
  });

  it("says the review screen could not load when Shopify's components did not arrive", () => {
    render(<ComponentsMissing />);
    expect(screen.getByRole("heading", { name: "The review screen could not load" })).toBeInTheDocument();
    expect(screen.getByText(/cdn\.shopify\.com/)).toBeInTheDocument();
  });
});

describe("Polaris wiring", () => {
  const routed = () =>
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<AppLink to="/proposals/p1">Open the fix</AppLink>} />
          <Route path="/proposals/:id" element={<p>Proposal page</p>} />
        </Routes>
      </MemoryRouter>,
    );

  it("keeps a plain click on a link inside the app", () => {
    routed();
    const link = polaris("s-link", "Open the fix")!;
    expect(link).toHaveAttribute("href", "/proposals/p1");
    expect(fireEvent.click(link)).toBe(false);
    expect(screen.getByText("Proposal page")).toBeInTheDocument();
  });

  it("leaves a click that asks for a new tab to the browser", () => {
    routed();
    expect(fireEvent.click(polaris("s-link", "Open the fix")!, { ctrlKey: true })).toBe(true);
    expect(screen.queryByText("Proposal page")).toBeNull();
  });

  it("keeps the /app prefix in the address, so a link opened in a new tab lands in the app", () => {
    render(
      <MemoryRouter basename="/app" initialEntries={["/app/"]}>
        <AppLink to="/proposals/p1">Open the fix</AppLink>
      </MemoryRouter>,
    );
    expect(polaris("s-link", "Open the fix")).toHaveAttribute("href", "/app/proposals/p1");
  });

  it.each([
    ["blocked", "critical", "Blocked"],
    ["failed", "critical", "Write failed"],
    ["applied", "success", "Written"],
    ["pending_review", "warning", "Needs review"],
    ["something_new", "neutral", "something_new"],
  ])("labels status %s with a %s badge", (status, tone, label) => {
    render(<StatusBadge status={status} />);
    expect(polaris("s-badge", label)).toHaveAttribute("tone", tone);
  });
});
