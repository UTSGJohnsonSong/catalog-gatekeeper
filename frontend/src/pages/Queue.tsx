import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type QueueItem } from "../api/client";
import { GateStrip } from "../components/GateStrip";
import { AppLink, ErrorBanner, Page, StatusBadge } from "../components/Page";

const FILTERS: { key: string; label: string; statuses: string[] | null }[] = [
  { key: "open", label: "Needs review", statuses: ["pending_review"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "failed", label: "Write failed", statuses: ["failed"] },
  { key: "done", label: "Decided", statuses: ["approved", "applying", "applied", "rejected", "stale"] },
  { key: "all", label: "All", statuses: null },
];

function OpsNotice() {
  const { data, error } = useQuery({ queryKey: ["ops"], queryFn: api.ops, refetchInterval: 10000 });
  if (error) return <ErrorBanner>Could not check the health of background work: {error.message}</ErrorBanner>;
  if (!data) return null;
  const notes = [
    data.failed_writes && `${data.failed_writes} write(s) failed`,
    data.stuck_writes && `${data.stuck_writes} write(s) look stuck`,
    data.dead_jobs && `${data.dead_jobs} background job(s) gave up`,
    data.failed_runs_today && `${data.failed_runs_today} agent run(s) ended without a result today`,
    data.webhooks_failed_today && `${data.webhooks_failed_today} webhook(s) could not be used today`,
  ].filter(Boolean);
  if (notes.length === 0) return null;
  return (
    <s-banner tone="warning" heading="Needs a person">
      {notes.join(", ")}. See the runbook.
    </s-banner>
  );
}

function Row({ item }: { item: QueueItem }) {
  const linkId = `open-${item.id}`;
  return (
    <s-table-row clickDelegate={linkId}>
      <s-table-cell>
        <AppLink id={linkId} to={`/proposals/${item.id}`}>
          {item.title}
        </AppLink>
      </s-table-cell>
      <s-table-cell>
        <GateStrip failed={item.failed_rules} />
      </s-table-cell>
      <s-table-cell>
        <StatusBadge status={item.status} />
      </s-table-cell>
      <s-table-cell>{item.risk_score}</s-table-cell>
    </s-table-row>
  );
}

export function QueuePage() {
  const client = useQueryClient();
  const [filter, setFilter] = useState("open");
  const { data, isLoading, error } = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 3000 });
  // Starting runs is an admin action. A reviewer, such as every visitor of the public demo, would only get a 403.
  const { data: me } = useQuery({ queryKey: ["whoami"], queryFn: api.whoami, retry: false });
  const run = useMutation({
    mutationFn: () => api.runAgent(10),
    onSuccess: () => client.invalidateQueries({ queryKey: ["queue"] }),
  });

  const active = FILTERS.find((candidate) => candidate.key === filter)!;
  const items = (data?.items ?? []).filter((item) => !active.statuses || active.statuses.includes(item.status));
  // Counts come from the server, because the list itself is capped.
  const counts = data?.counts ?? {};
  const count = (statuses: string[] | null) =>
    (statuses ?? Object.keys(counts)).reduce((total, status) => total + (counts[status] ?? 0), 0);
  const label = (option: (typeof FILTERS)[number]) => (data ? `${option.label} (${count(option.statuses)})` : option.label);

  const audit =
    me?.role === "admin" ? (
      <s-button slot="primary-action" variant="primary" loading={run.isPending} onClick={() => run.mutate()}>
        Audit more products
      </s-button>
    ) : undefined;

  return (
    <Page heading="Review queue" primaryAction={audit}>
      <s-stack gap="base">
        <OpsNotice />
        {run.isError && <ErrorBanner>Could not start the audit: {run.error.message}</ErrorBanner>}
        {run.isSuccess && (
          <s-banner tone="success">Queued {run.data.queued} product(s). New fixes appear here within seconds.</s-banner>
        )}
        {error && <ErrorBanner>Could not load the queue: {error.message}</ErrorBanner>}
        <s-section padding="none" accessibilityLabel="Proposed fixes">
          {/* Polaris has no tabs. The admin's own filter pills are a filled button for the current view. */}
          <div className="filters" role="group" aria-label="Filter by status">
            {FILTERS.map((option) => (
              <s-button
                key={option.key}
                variant={filter === option.key ? "secondary" : "tertiary"}
                accessibilityLabel={`${label(option)}${filter === option.key ? ", selected" : ""}`}
                onClick={() => setFilter(option.key)}
              >
                {label(option)}
              </s-button>
            ))}
          </div>
          <s-table loading={isLoading}>
            <s-table-header-row>
              <s-table-header listSlot="primary">Product</s-table-header>
              <s-table-header listSlot="labeled">Checks</s-table-header>
              <s-table-header listSlot="inline">Status</s-table-header>
              <s-table-header listSlot="labeled" format="numeric">
                Risk
              </s-table-header>
            </s-table-header-row>
            <s-table-body>
              {items.map((item) => (
                <Row key={item.id} item={item} />
              ))}
            </s-table-body>
          </s-table>
          {data && items.length === 0 && (
            <div className="empty">
              <s-paragraph color="subdued">Nothing here. Choose another filter, or audit more products.</s-paragraph>
            </div>
          )}
        </s-section>
        <s-paragraph color="subdued">
          Each row shows the 12 checks in order. A red tick stopped the fix. An ochre tick is a warning for the reviewer.
        </s-paragraph>
      </s-stack>
    </Page>
  );
}
