import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, type ProposalDetail } from "../api/client";
import { DecisionBar } from "../components/DecisionBar";
import { DiffPanel } from "../components/DiffPanel";
import { Gate } from "../components/Gate";

const STATUS_NAMES: Record<string, string> = {
  pending_review: "Needs review",
  blocked: "Blocked",
  approved: "Approved",
  applying: "Writing",
  applied: "Written",
  rejected: "Rejected",
  stale: "Out of date",
  failed: "Write failed",
};

export function statusName(status: string): string {
  return STATUS_NAMES[status] ?? status;
}

function ModelLine({ proposal }: { proposal: ProposalDetail }) {
  if (proposal.model_mode === "demo") {
    return <p className="hint">Drafted by {proposal.model}, a template script. No LLM wrote this.</p>;
  }
  return (
    <p className="hint">
      Drafted by {proposal.model ?? "an unknown model"}.{" "}
      {proposal.judge_status === "disabled" ? "The LLM judge did not run." : `Judge status: ${proposal.judge_status}.`}
    </p>
  );
}

export function ProposalPage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const { data: proposal, isLoading, error: loadError } = useQuery({
    queryKey: ["proposal", id],
    queryFn: () => api.proposal(id),
    refetchInterval: (query) => (["approved", "applying"].includes(query.state.data?.status ?? "") ? 1000 : false),
  });

  const settle = () => {
    client.invalidateQueries({ queryKey: ["proposal", id] });
    client.invalidateQueries({ queryKey: ["queue"] });
  };
  const onError = (failure: Error) => {
    if (failure instanceof ApiError && failure.status === 409) {
      setError(`${failure.message}. The page now shows the latest state, so review it again.`);
    } else {
      setError(failure.message);
    }
    settle();
  };
  const approve = useMutation({
    mutationFn: (confirm: boolean) => api.approve(id, proposal?.review_hash ?? "", confirm),
    onSuccess: settle,
    onError,
  });
  const reject = useMutation({ mutationFn: (note: string) => api.reject(id, note), onSuccess: settle, onError });

  if (isLoading) return <p className="hint">Loading the proposal.</p>;
  if (loadError || !proposal) {
    return (
      <p className="error" role="alert">
        Could not load this proposal: {loadError?.message ?? "not found"}. <Link to="/">Go back to the queue.</Link>
      </p>
    );
  }

  return (
    <div className="proposal">
      <div className="proposal-main">
        <p className="crumb">
          <Link to="/">Review queue</Link>
        </p>
        <h1 className="catalog-voice">{proposal.product_title}</h1>
        <p className={`status status-${proposal.status}`}>
          {statusName(proposal.status)}, risk {proposal.risk_score}
        </p>
        <ModelLine proposal={proposal} />
        {proposal.risk_reasons.length > 0 && <p className="hint">Risk comes from: {proposal.risk_reasons.join("; ")}.</p>}

        <h2>What would change</h2>
        <DiffPanel diffs={proposal.diffs} />

        {proposal.rationale && (
          <>
            <h2>Why the fix was proposed</h2>
            <p>{proposal.rationale}</p>
          </>
        )}

        {proposal.citations.length > 0 && (
          <>
            <h2>Store policy it relied on</h2>
            <ul className="citations">
              {proposal.citations.map((citation) => (
                <li key={`${citation.chunk_id}${citation.quote}`}>
                  <blockquote className="catalog-voice">{citation.quote}</blockquote>
                  <p className={citation.verified ? "verified" : "unverified"}>
                    {citation.verified
                      ? `Matches ${citation.chunk_id} word for word, and that passage was retrieved during this run.`
                      : `Could not be verified against ${citation.chunk_id || "the policy documents"} (${citation.reason}).`}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )}

        <h2>Open findings on this product</h2>
        {proposal.findings.filter((f) => f.status === "open").length === 0 ? (
          <p className="hint">None. Every audit rule passes on the current product.</p>
        ) : (
          <ul className="findings">
            {proposal.findings
              .filter((f) => f.status === "open")
              .map((f) => (
                <li key={`${f.rule_id}${f.subject}`}>
                  <span className="gate-id">{f.rule_id}</span> {f.name.toLowerCase().replaceAll("_", " ")},{" "}
                  {f.severity} severity{f.fixable ? "" : ", needs a person"}
                </li>
              ))}
          </ul>
        )}
      </div>

      <aside className="gate" aria-label="Checks and decision">
        <h2>The gate</h2>
        <Gate rows={proposal.validation} />
        <DecisionBar
          proposal={proposal}
          busy={approve.isPending || reject.isPending}
          error={error}
          onApprove={(confirm) => {
            setError(null);
            approve.mutate(confirm);
          }}
          onReject={(note) => {
            setError(null);
            reject.mutate(note);
          }}
        />
      </aside>
    </div>
  );
}
