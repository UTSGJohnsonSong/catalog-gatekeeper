import { diffWords } from "diff";
import type { FieldDiff } from "../api/client";

const FIELD_NAMES: Record<string, string> = {
  description_html: "Description (HTML source)",
  seo_title: "Search title",
  seo_description: "Search description",
};

// HTML is rendered as escaped text on purpose. Catalog content is never injected into the page as markup.
export function DiffPanel({ diffs }: { diffs: FieldDiff[] }) {
  return (
    <div className="diffs">
      {diffs.map((field) => (
        <section key={field.field} className="diff">
          <h3>{FIELD_NAMES[field.field] ?? field.field}</h3>
          <p className="catalog-voice diff-body" data-testid={`diff-${field.field}`}>
            {diffWords(field.before, field.after).map((part, index) => {
              if (part.added) return <ins key={index}>{part.value}</ins>;
              if (part.removed) return <del key={index}>{part.value}</del>;
              return <span key={index}>{part.value}</span>;
            })}
          </p>
          {field.before === "" && <p className="hint">This field was empty, so all of the text is new.</p>}
        </section>
      ))}
    </div>
  );
}
