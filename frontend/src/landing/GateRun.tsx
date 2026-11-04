import { useEffect, useState } from "react";

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

const STEP_MS = 260;

function stateOf(check: Check): "passed" | "warned" | "stopped" {
  if (check.passed) return "passed";
  return check.severity === "warn" ? "warned" : "stopped";
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
  const warned = checks.filter((check) => stateOf(check) === "warned").length;
  return warned > 0
    ? `Passed with ${warned} warning(s). Waiting for a person to approve.`
    : `All ${checks.length} checks passed. Waiting for a person to approve.`;
}

// The signature moment: one real fix walks through the 12 checks once, on load. With reduced motion, or when
// `animate` is off, the finished state is shown at once.
export function GateRun({ checks, animate = true }: { checks: Check[]; animate?: boolean }) {
  const [shown, setShown] = useState(() => (animate && !prefersReducedMotion() ? 0 : checks.length));

  useEffect(() => {
    if (shown >= checks.length) return;
    const timer = window.setTimeout(() => setShown((count) => count + 1), STEP_MS);
    return () => window.clearTimeout(timer);
  }, [shown, checks.length]);

  const done = shown >= checks.length;
  const current = checks[Math.min(shown, checks.length - 1)];
  // The moving parts are hidden from screen readers, which get the result once instead of twelve updates.
  return (
    <div className="gate-run">
      <ol className="gates" aria-hidden="true">
        {checks.map((check, index) => (
          <li
            key={check.rule_id}
            className={`gate gate-${index < shown ? stateOf(check) : "waiting"}`}
            title={CHECK_NAMES[check.rule_id]}
          >
            <span className="gate-bar" />
            <span className="gate-id">{check.rule_id}</span>
          </li>
        ))}
      </ol>
      <p className={`gate-caption${done ? " gate-caption-done" : ""}`} aria-hidden="true">
        {done ? outcome(checks) : `Checking ${current.rule_id}: ${CHECK_NAMES[current.rule_id]?.toLowerCase()}`}
      </p>
      <p className="sr-only">{outcome(checks)}</p>
    </div>
  );
}
