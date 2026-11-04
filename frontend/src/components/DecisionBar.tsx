import { useState } from "react";
import type { ProposalDetail } from "../api/client";
import { ErrorBanner } from "./Page";

const HIGH_RISK = 50;

const STATUS_SENTENCE: Record<string, string> = {
  approved: "Approved. The change is waiting to be written to the store.",
  applying: "Writing the change to the store.",
  applied: "Written to the store and verified.",
  rejected: "Rejected. Nothing was written.",
  stale: "The product changed before this fix could be written, so it was not written.",
  failed: "The write did not finish cleanly. The store may or may not hold this text, so compare it with the store before acting.",
};

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return <ErrorBanner>{error}</ErrorBanner>;
}

// Polaris fields report their value on the element itself.
const valueOf = (event: Event) => (event.currentTarget as HTMLInputElement).value;

type Props = {
  proposal: ProposalDetail;
  busy: boolean;
  error: string | null;
  writesEnabled?: boolean;
  onApprove: (confirmHighRisk: boolean) => void;
  onReject: (note: string) => void;
};

export function DecisionBar({ proposal, busy, error, writesEnabled = true, onApprove, onReject }: Props) {
  const [confirmation, setConfirmation] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");

  const stopped = proposal.validation.filter((row) => !row.passed && row.severity === "block").map((row) => row.rule_id);
  const outcome =
    proposal.status === "blocked"
      ? `Stopped by ${stopped.join(", ")}. A blocked fix cannot be approved.`
      : proposal.status === "pending_review"
        ? ""
        : (STATUS_SENTENCE[proposal.status] ?? proposal.status);
  // Stays mounted whatever the status, so the outcome of an approval is announced after the buttons are gone.
  const announcer = (
    <p className="sr-only" aria-live="polite">
      {outcome}
    </p>
  );

  if (proposal.status === "blocked") {
    return (
      <div className="decision" data-testid="decision">
        {announcer}
        <s-stack gap="base">
          <ErrorLine error={error} />
          <s-banner tone="critical">{outcome}</s-banner>
        </s-stack>
      </div>
    );
  }
  if (proposal.status !== "pending_review") {
    return (
      <div className="decision" data-testid="decision">
        {announcer}
        <s-stack gap="base">
          <ErrorLine error={error} />
          <s-paragraph>{outcome}</s-paragraph>
          {proposal.writeback_errors.map((item, index) => (
            <s-paragraph tone="critical" key={index}>
              {String(item.message ?? JSON.stringify(item))}
            </s-paragraph>
          ))}
        </s-stack>
      </div>
    );
  }

  const highRisk = proposal.risk_score >= HIGH_RISK;
  const canApprove = !busy && (!highRisk || confirmation === "APPROVE");
  const canReject = !busy && note.trim().length >= 5;
  // The handlers repeat the checks behind `disabled`, so the typed APPROVE never depends on Polaris alone.
  return (
    <div className="decision" data-testid="decision">
      {announcer}
      <s-stack gap="base">
        {highRisk && (
          <s-text-field
            label={`Risk is ${proposal.risk_score}. Type APPROVE to confirm.`}
            value={confirmation}
            autocomplete="off"
            onInput={(event: Event) => setConfirmation(valueOf(event))}
          />
        )}
        <ErrorLine error={error} />
        <s-stack direction="inline" gap="small-200">
          <s-button variant="primary" disabled={!canApprove} onClick={() => canApprove && onApprove(highRisk)}>
            {writesEnabled ? "Approve and write to store" : "Approve (writing is switched off, so it will wait)"}
          </s-button>
          <s-button disabled={busy} onClick={() => !busy && setRejecting(!rejecting)}>
            Reject
          </s-button>
        </s-stack>
        {rejecting && (
          <s-stack gap="small-200">
            <s-text-area
              label="Why are you rejecting this fix?"
              rows={3}
              value={note}
              onInput={(event: Event) => setNote(valueOf(event))}
            />
            <s-button tone="critical" disabled={!canReject} onClick={() => canReject && onReject(note)}>
              Reject this fix
            </s-button>
          </s-stack>
        )}
      </s-stack>
    </div>
  );
}
