// The signature element: the same 12 checks, always in the same order.
export const CHECK_IDS = ["V01", "V02", "V03", "V04", "V05", "V06", "V07", "V08", "V09", "V10", "V11", "V12"];
export const WARNING_ONLY = new Set(["V10"]);

export function tickState(id: string, failed: Set<string>): "passed" | "stopped" | "warned" {
  if (!failed.has(id)) return "passed";
  return WARNING_ONLY.has(id) ? "warned" : "stopped";
}

export function GateStrip({ failed }: { failed: string[] }) {
  const set = new Set(failed);
  const stopped = CHECK_IDS.filter((id) => tickState(id, set) === "stopped");
  const label =
    stopped.length === 0 ? "All blocking checks passed" : `Stopped by ${stopped.join(", ")}`;
  return (
    <span className="gate-strip" role="img" aria-label={label} title={label}>
      {CHECK_IDS.map((id) => (
        <span key={id} className={`tick tick-${tickState(id, set)}`} />
      ))}
    </span>
  );
}
