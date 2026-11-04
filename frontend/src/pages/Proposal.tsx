import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api, type ProposalDetail } from "../api/client";
import { DecisionBar } from "../components/DecisionBar";
import { DiffPanel } from "../components/DiffPanel";
import { Gate } from "../components/Gate";
import { AppLink, ErrorBanner, Page, StatusBadge } from "../components/Page";

const JUDGE_SENTENCE: Record<string, string> = {
  disabled: "The LLM judge did not run.",
  skipped_blocked: "The LLM judge did not run, because a check had already stopped this fix.",
  ok: "The LLM judge ran. It can raise risk, but it cannot clear a check.",
  unavailable: "The LLM judge failed, so nobody has assessed this. Risk was raised to reflect that.",
};

function ModelLine({ proposal }: { proposal: ProposalDetail }) {
  if (proposal.model_mode === "demo") {
    return <s-paragraph color="subdued">Drafted by {proposal.model}, a template script. No LLM wrote this.</s-paragraph>;
  }
  return (
    <s-paragraph color="subdued">
      Drafted by {proposal.model ?? "an unknown model"}. {JUDGE_SENTENCE[proposal.judge_status] ?? proposal.judge_status}
      {proposal.judge_discarded > 0 && ` It raised ${proposal.judge_discarded} concern(s) with quotes that could not be found, so they were dropped.`}
    </s-paragraph>
  );
}

const FLAG_SENTENCE: Record<string, string> = {
  injection_suspected: "The model reported that this product's text tried to instruct it.",
  insufficient_source_facts: "The model reported too few facts in the product to write a full description.",
  policy_gap: "The model reported that the policy documents do not cover this.",
  demo_adversarial: "This demo product makes the template produce a deliberately bad fix.",
};

export function ProposalPage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
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

  const back = { to: "/", label: "Review queue" };
  if (isLoading) {
    return (
      <Page heading="Proposed fix" breadcrumb={back}>
        <s-paragraph color="subdued">Loading the proposal.</s-paragraph>
      </Page>
    );
  }
  if (!proposal) {
    return (
      <Page heading="Proposed fix" breadcrumb={back}>
        <ErrorBanner>
          Could not load this proposal: {loadError?.message ?? "not found"}. <AppLink to="/">Go back to the queue.</AppLink>
        </ErrorBanner>
      </Page>
    );
  }

  const openFindings = proposal.findings.filter((f) => f.status === "open");
  // Polaris draws a section in the aside without padding, so the gate brings its own box.
  const gate = (
    <s-section slot="aside" heading="The gate">
      <s-box padding="base" background="base" borderRadius="large-200">
        <Gate rows={proposal.validation} />
        <DecisionBar
          proposal={proposal}
          busy={approve.isPending || reject.isPending}
          error={error}
          writesEnabled={meta ? meta.writeback_env_enabled && meta.writeback_shop_enabled : true}
          onApprove={(confirm) => {
            setError(null);
            approve.mutate(confirm);
          }}
          onReject={(note) => {
            setError(null);
            reject.mutate(note);
          }}
        />
      </s-box>
    </s-section>
  );

  return (
    <Page heading={proposal.product_title} breadcrumb={back} aside={gate}>
      {loadError && (
        <ErrorBanner>Could not refresh this proposal: {loadError.message}. What you see may be out of date.</ErrorBanner>
      )}
      <s-section>
        <s-stack gap="small-200">
          <s-stack direction="inline" gap="small-200" alignItems="center">
            <StatusBadge status={proposal.status} />
            <s-text>Risk {proposal.risk_score}</s-text>
          </s-stack>
          <ModelLine proposal={proposal} />
          {proposal.run_flags.map((flag) => (
            <s-paragraph tone="caution" key={flag}>
              {FLAG_SENTENCE[flag] ?? flag}
            </s-paragraph>
          ))}
          {proposal.risk_reasons.length > 0 && (
            <s-paragraph color="subdued">Risk comes from: {proposal.risk_reasons.join("; ")}.</s-paragraph>
          )}
        </s-stack>
      </s-section>

      <s-section heading="What would change">
        <DiffPanel diffs={proposal.diffs} />
      </s-section>

      {proposal.rationale && (
        <s-section heading="Why the fix was proposed">
          <s-paragraph>{proposal.rationale}</s-paragraph>
        </s-section>
      )}

      {proposal.citations.length > 0 && (
        <s-section heading="Store policy it relied on">
          <ul className="citations">
            {proposal.citations.map((citation) => (
              <li key={`${citation.chunk_id}${citation.quote}`}>
                <blockquote>{citation.quote}</blockquote>
                <s-paragraph tone={citation.verified ? "success" : "critical"}>
                  {citation.verified
                    ? `Matches ${citation.chunk_id} word for word, and that passage was retrieved during this run.`
                    : `Could not be verified against ${citation.chunk_id || "the policy documents"} (${citation.reason}).`}
                </s-paragraph>
              </li>
            ))}
          </ul>
        </s-section>
      )}

      <s-section heading="Open findings on this product">
        {openFindings.length === 0 ? (
          <s-paragraph color="subdued">None. Every audit rule passes on the current product.</s-paragraph>
        ) : (
          <s-unordered-list>
            {openFindings.map((f) => (
              <s-list-item key={`${f.rule_id}${f.subject}`}>
                {f.rule_id} {f.name.toLowerCase().replaceAll("_", " ")}, {f.severity} severity{f.fixable ? "" : ", needs a person"}
              </s-list-item>
            ))}
          </s-unordered-list>
        )}
      </s-section>
    </Page>
  );
}
