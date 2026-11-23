import { diffWords } from "diff";
import type sample from "./sample.json";

export type Fix = (typeof sample.fixes)[number];

// Catalog text is shown as text, never as markup, as in the review screen.
export function FixText({ fix }: { fix: Fix }) {
  return (
    <p className="fix-text">
      {diffWords(fix.before.join("\n"), fix.after.join("\n")).map((part, index) => {
        if (part.added) return <ins key={index}>{part.value}</ins>;
        if (part.removed) return <del key={index}>{part.value}</del>;
        return <span key={index}>{part.value}</span>;
      })}
    </p>
  );
}
