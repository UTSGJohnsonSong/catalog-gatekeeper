import { AUDIT_RULES, type Severity } from "./content";
import gate from "./gate.json";
import { CHECK_NAMES, type GateResult } from "./GateRun";
import rules from "./rules.json";
import { useTabs } from "./tabs";

// "What it looks for". The thirteen audit rules are tiles: choosing one shows when it fires and a real example,
// found by running that rule over the simulated store (rules.json, written by scripts/build_page_samples.py). The
// twelve checks open as a list under them, and the ones the gate at the top can trip link back to it.

const EXAMPLES: Record<string, { product: string; found: string }> = rules.examples;
const RESULTS: Record<string, GateResult> = gate.results;

// The plain draft's answer names every check in order and says which only warn.
const CHECKS = RESULTS[""].checks;

// The checks the gate lets a visitor trip: every check that stops some combination in gate.json.
const IN_THE_GATE = new Set(
  Object.values(RESULTS).flatMap((result) => result.stopped_by.map((stop) => stop.rule_id)),
);

const SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];

// The word carries the severity; the four pips repeat it as a length.
function SeverityMark({ level }: { level: Severity }) {
  const filled = SEVERITIES.indexOf(level) + 1;
  return (
    <p className="severity">
      <span className="pips" aria-hidden="true">
        {SEVERITIES.map((name, index) => (
          <span key={name} className={index < filled ? "pip pip-on" : "pip"} />
        ))}
      </span>
      {level[0].toUpperCase() + level.slice(1)} severity
    </p>
  );
}

export function Rules() {
  // One rule is open at first, so the tiles read as something to press and a visitor sees an example from the
  // store without doing anything. A03 is the plainest one: a description that only says "Coming soon".
  const tabs = useTabs("rule", AUDIT_RULES.length, {
    initial: AUDIT_RULES.findIndex((rule) => rule.id === "A03"),
  });
  return (
    <>
      {/* The key says in words what the two tile outlines mean, and each tile points at its entry. */}
      <ul className="rule-key" role="list">
        <li id="rule-key-agent">
          <span className="swatch tile-agent" aria-hidden="true" />
          The agent may draft a fix
        </li>
        <li id="rule-key-person">
          <span className="swatch tile-person" aria-hidden="true" />
          Left to a person
        </li>
      </ul>
      <div className="rule-tiles" role="tablist" aria-label="The thirteen audit rules" onKeyDown={tabs.onKeyDown}>
        {AUDIT_RULES.map((rule, index) => (
          <button
            key={rule.id}
            type="button"
            className={rule.agentMayFix ? "tile tile-agent" : "tile tile-person"}
            aria-describedby={rule.agentMayFix ? "rule-key-agent" : "rule-key-person"}
            {...tabs.tab(index)}
          >
            <span className="tile-id">{rule.id}</span> {rule.name}
          </button>
        ))}
      </div>

      {AUDIT_RULES.map((rule, index) => (
        <div key={rule.id} className="docket rule-panel" {...tabs.panel(index)}>
          <h3>
            {rule.id} {rule.name}
          </h3>
          <p>Fires when {rule.when}</p>
          <SeverityMark level={rule.severity} />
          <p>
            In the simulated store: {EXAMPLES[rule.id].product}. {EXAMPLES[rule.id].found}
          </p>
        </div>
      ))}

      <details className="checks">
        <summary>Twelve checks on every fix</summary>
        <ul className="check-list" role="list">
          {CHECKS.map((check) => (
            <li key={check.rule_id}>
              <span className="check-id">{check.rule_id}</span>
              <span>
                {IN_THE_GATE.has(check.rule_id) ? (
                  <a href="#gate">{CHECK_NAMES[check.rule_id]}</a>
                ) : (
                  CHECK_NAMES[check.rule_id]
                )}
                {check.severity === "warn" && (
                  <>
                    {" "}
                    <span className="tag">warns only</span>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
        <p className="note">
          Every check runs on every fix, so the reviewer sees all the evidence. A check marked &quot;warns only&quot;
          cannot stop a fix; every other check can. A linked check is one you can trip in the gate at the top. The
          rules and the checks are all deterministic code, not model calls.
        </p>
      </details>
    </>
  );
}
