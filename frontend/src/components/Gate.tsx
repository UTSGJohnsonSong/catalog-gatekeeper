import type { ValidationRow } from "../api/client";
import { CHECK_IDS, tickState } from "./GateStrip";

const CHECK_PLAIN_NAMES: Record<string, string> = {
  V01: "Only allowed fields change",
  V02: "Product has not changed since",
  V03: "HTML is safe",
  V04: "No new links or contact details",
  V05: "Every number comes from the product",
  V06: "No medical, comparison or price claims",
  V07: "Policy statements have a citation",
  V08: "Citations match the policy text",
  V09: "Existing facts are kept",
  V10: "Rewrite stays close to the original",
  V11: "No prompt injection residue",
  V12: "Fixes a finding and adds none",
  J00: "Judge could not run",
  J01: "Judge: claim the source does not support",
  J02: "Judge: meaning changed",
  J03: "Judge: tone",
};

function evidenceText(row: ValidationRow): string | null {
  const entries = Object.entries(row.evidence).filter(([, value]) => {
    if (Array.isArray(value)) return value.length > 0;
    return value !== null && value !== undefined && value !== "";
  });
  if (entries.length === 0) return null;
  return JSON.stringify(Object.fromEntries(entries), null, 1);
}

export function Gate({ rows }: { rows: ValidationRow[] }) {
  const byId = new Map(rows.map((row) => [row.rule_id, row]));
  const failed = new Set(rows.filter((row) => !row.passed).map((row) => row.rule_id));
  const judgeRows = rows.filter((row) => row.kind === "judge");
  return (
    <ol className="gate-list">
      {CHECK_IDS.map((id) => {
        const row = byId.get(id);
        const state = tickState(id, failed);
        const evidence = row ? evidenceText(row) : null;
        return (
          <li key={id} className={`gate-row gate-${state}`}>
            <details open={state !== "passed"}>
              <summary>
                <span className={`tick tick-${state}`} aria-hidden="true" />
                <span className="gate-id">{id}</span>
                <span>{CHECK_PLAIN_NAMES[id]}</span>
                <span className="sr-only">{state}</span>
              </summary>
              {evidence ? <pre className="evidence">{evidence}</pre> : <p className="hint">Nothing to report.</p>}
            </details>
          </li>
        );
      })}
      {judgeRows.map((row) => (
        <li key={row.rule_id} className="gate-row gate-warned">
          <details open>
            <summary>
              <span className="tick tick-warned" aria-hidden="true" />
              <span className="gate-id">{row.rule_id}</span>
              <span>{CHECK_PLAIN_NAMES[row.rule_id] ?? row.name}</span>
            </summary>
            <pre className="evidence">{JSON.stringify(row.evidence, null, 1)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}
