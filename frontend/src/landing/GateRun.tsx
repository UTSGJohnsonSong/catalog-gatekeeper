import { useEffect, useState } from "react";
import { CheckIcon, CrossIcon } from "./Icons";

// The names the review screen shows for the twelve checks (frontend/src/components/Gate.tsx).
export const CHECK_NAMES: Record<string, string> = {
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
};

export type Check = { rule_id: string; passed: boolean; severity: string };

// What a check that stopped a fix found in it.
export type Stop = { rule_id: string; found: string };

// One recorded answer in gate.json: the twelve checks for one fix, and what each stopping check found.
export type GateResult = { checks: Check[]; stopped_by: Stop[] };

export type Light = { id: string; state: "idle" | "passed" | "flagged" | "stopped"; title?: string };

// Time between two lights. Twelve take a little over a second, so a run answers a tap without a wait.
export const STEP_MS = 110;

function stateOf(check: Check): "passed" | "flagged" | "stopped" {
  if (check.passed) return "passed";
  return check.severity === "warn" ? "flagged" : "stopped";
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function outcome(checks: Check[]): string {
  const stopped = checks.filter((check) => stateOf(check) === "stopped").map((check) => check.rule_id);
  if (stopped.length > 0) return `Stopped by ${stopped.join(", ")}. Nobody can approve it.`;
  const warned = checks.filter((check) => stateOf(check) === "flagged").length;
  if (warned > 0) return `Clears with ${warned} ${warned === 1 ? "warning" : "warnings"}. Now a person decides.`;
  return `Clears all ${checks.length}. Now a person decides.`;
}

// How many of a fix's checks have lit so far. The run plays once for each fix: a new `checks` array starts it again
// from the first check. With reduced motion, or with `animate` off, every check is lit at once.
export function useRun(checks: Check[], animate = true): number {
  const start = () => (animate && !prefersReducedMotion() ? 0 : checks.length);
  const [run, setRun] = useState(() => ({ checks, shown: start() }));
  // Reset while rendering, not in an effect, so the last fix's lights never show against the new fix.
  if (run.checks !== checks) setRun({ checks, shown: start() });

  useEffect(() => {
    if (run.shown >= run.checks.length) return;
    const timer = window.setTimeout(() => setRun({ checks: run.checks, shown: run.shown + 1 }), STEP_MS);
    return () => window.clearTimeout(timer);
  }, [run]);

  return run.shown;
}

// One light for each check, in the fixed order. The first `shown` carry their result; the rest wait.
export function lightsOf(checks: Check[], shown = checks.length): Light[] {
  return checks.map((check, index) => ({
    id: check.rule_id,
    state: index < shown ? stateOf(check) : "idle",
    title: CHECK_NAMES[check.rule_id],
  }));
}

// A row of bars in a fixed order. A stopped one is taller and wider, so shape carries it as well as colour. The row
// is drawing only: the result is always given in words next to it, so screen readers skip the bars.
export function Lights({ items }: { items: Light[] }) {
  return (
    <ol className="lights" aria-hidden="true">
      {items.map((item) => (
        <li key={item.id} className={`light light-${item.state}`} title={item.title}>
          <span className="light-bar" />
          <span className="light-id">{item.id}</span>
        </li>
      ))}
    </ol>
  );
}

// The result in words: what the gate decided and, for a stopped fix, each check that stopped it with what it found.
export function Verdict({ checks, stops = [] }: { checks: Check[]; stops?: Stop[] }) {
  const stopped = checks.some((check) => stateOf(check) === "stopped");
  return (
    <>
      <p className={stopped ? "verdict verdict-stopped" : "verdict verdict-cleared"}>
        {stopped ? <CrossIcon /> : <CheckIcon />}
        {outcome(checks)}
      </p>
      {stops.length > 0 && (
        <ul className="verdict-stops" role="list">
          {stops.map((stop) => (
            <li key={stop.rule_id}>
              <span className="check-id">{stop.rule_id}</span> {CHECK_NAMES[stop.rule_id]}. It found{" "}
              &quot;{stop.found}&quot;.
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
