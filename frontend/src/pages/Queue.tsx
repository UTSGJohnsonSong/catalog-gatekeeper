import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type QueueItem } from "../api/client";
import { GateStrip } from "../components/GateStrip";
import { statusName } from "./Proposal";

const FILTERS: { key: string; label: string; statuses: string[] | null }[] = [
  { key: "open", label: "Needs review", statuses: ["pending_review"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "done", label: "Decided", statuses: ["approved", "applying", "applied", "rejected", "stale", "failed"] },
  { key: "all", label: "All", statuses: null },
];

function Row({ item }: { item: QueueItem }) {
  return (
    <li className="queue-row">
      <Link to={`/proposals/${item.id}`} className="queue-title catalog-voice">
        {item.title}
      </Link>
      <GateStrip failed={item.failed_rules} />
      <span className={`status status-${item.status}`}>{statusName(item.status)}</span>
      <span className="queue-risk">Risk {item.risk_score}</span>
    </li>
  );
}

export function QueuePage() {
  const client = useQueryClient();
  const [filter, setFilter] = useState("open");
  const { data, isLoading, error } = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 3000 });
  const run = useMutation({
    mutationFn: () => api.runAgent(10),
    onSuccess: () => client.invalidateQueries({ queryKey: ["queue"] }),
  });

  const active = FILTERS.find((candidate) => candidate.key === filter)!;
  const items = (data?.items ?? []).filter((item) => !active.statuses || active.statuses.includes(item.status));
  const count = (statuses: string[] | null) =>
    (data?.items ?? []).filter((item) => !statuses || statuses.includes(item.status)).length;

  return (
    <>
      <div className="page-head">
        <h1>Review queue</h1>
        <button onClick={() => run.mutate()} disabled={run.isPending}>
          Audit more products
        </button>
      </div>
      {run.isError && (
        <p className="error" role="alert">
          Could not start the audit: {run.error.message}
        </p>
      )}
      {run.isSuccess && <p className="hint">Queued {run.data.queued} product(s). New fixes appear here within seconds.</p>}
      <div className="filters" role="group" aria-label="Filter by status">
        {FILTERS.map((option) => (
          <button key={option.key} aria-pressed={filter === option.key} onClick={() => setFilter(option.key)}>
            {option.label} ({count(option.statuses)})
          </button>
        ))}
      </div>
      {isLoading && <p className="hint">Loading the queue.</p>}
      {error && (
        <p className="error" role="alert">
          Could not load the queue: {error.message}
        </p>
      )}
      {data && items.length === 0 && <p className="hint">Nothing here. Choose another filter, or audit more products.</p>}
      <ul className="queue">
        {items.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </ul>
      <p className="hint gate-legend">
        Each row shows the 12 checks in order. A red tick stopped the fix. An ochre tick is a warning for the reviewer.
      </p>
    </>
  );
}
