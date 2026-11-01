import { useState } from "react";
import type { ProposalDetail } from "../api/client";

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
  return (
    <p className="error" role="alert">
      {error}
    </p>
  );
}

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

  if (proposal.status === "blocked") {
    const stopped = proposal.validation.filter((row) => !row.passed && row.severity === "block").map((row) => row.rule_id);
    return (
      <div className="decision" data-testid="decision">
        <ErrorLine error={error} />
        <p className="decision-stopped">Stopped by {stopped.join(", ")}. A blocked fix cannot be approved.</p>
      </div>
    );
  }
  if (proposal.status !== "pending_review") {
    return (
      <div className="decision" data-testid="decision">
        <ErrorLine error={error} />
        <p>{STATUS_SENTENCE[proposal.status] ?? proposal.status}</p>
        {proposal.writeback_errors.map((item, index) => (
          <p className="error" key={index}>
            {String(item.message ?? JSON.stringify(item))}
          </p>
        ))}
      </div>
    );
  }

  const highRisk = proposal.risk_score >= HIGH_RISK;
  const canApprove = !busy && (!highRisk || confirmation === "APPROVE");
  return (
    <div className="decision" data-testid="decision">
      {highRisk && (
        <label className="field">
          Risk is {proposal.risk_score}. Type APPROVE to confirm.
          <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" />
        </label>
      )}
      <ErrorLine error={error} />
      <div className="decision-actions">
        <button className="primary" disabled={!canApprove} onClick={() => onApprove(highRisk)}>
          {writesEnabled ? "Approve and write to store" : "Approve (writing is switched off, so it will wait)"}
        </button>
        <button disabled={busy} onClick={() => setRejecting(!rejecting)} aria-expanded={rejecting}>
          Reject
        </button>
      </div>
      {rejecting && (
        <form
          className="reject-form"
          onSubmit={(event) => {
            event.preventDefault();
            onReject(note);
          }}
        >
          <label className="field">
            Why are you rejecting this fix?
            <textarea value={note} rows={3} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button type="submit" disabled={busy || note.trim().length < 5}>
            Reject this fix
          </button>
        </form>
      )}
    </div>
  );
}
