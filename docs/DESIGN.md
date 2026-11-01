# Review UI design plan

The owner's rule is to see a design plan before building. The owner is unavailable until after 2026-11-03 and delegated this build, so the plan was decided here and can be changed afterwards.

**Subject and job.** A merchant or catalog manager decides whether a proposed product-text change goes live. The page must make it obvious what would change, what checked it, and why a blocked fix cannot be approved.

## Palette

| Name | Light | Dark | Used for |
|---|---|---|---|
| Inspection paper | `#EDF1F3` | `#151C29` | Page background |
| Docket ink | `#17243B` | `#E4EAF2` | Text |
| Harbour blue | `#24508F` | `#8DB2EA` | Actions, links |
| Cleared green | `#1B7354` | `#58C39A` | Passed checks, added text |
| Stopped red | `#AE2640` | `#F08595` | Blocking failures, removed text |
| Hold ochre | `#94600A` | `#E0B060` | Warnings, risk |

## Type

- Familjen Grotesk for the system's voice: navigation, labels, numbers, buttons.
- Newsreader for the catalog's voice: anything that came from a product or a policy document.
- The split carries meaning. The reader can always tell who is speaking, the tool or the store. Both are bundled with the app, so no request leaves for a font CDN.

## Layout

```
Queue                                  Proposal
+--------------------------------+     +----------------------+----------+
| Title        gate strip   risk |     | Before / after       | The gate |
| Title        gate strip   risk |     | per changed field    | 12 checks|
| Title        gate strip   risk |     | Citations            | Approve  |
+--------------------------------+     +----------------------+----------+
```

Left aligned throughout. On a phone the gate stacks under the diff, and the decision bar stays at the end of the gate.

## Signature element: the gate

Every proposal passes the same 12 deterministic checks. The gate strip draws them as 12 ticks in a fixed order, the same on the queue row and the proposal page, so a reviewer learns the pattern: a taller red tick is a stopped check, and its position says which one. On the proposal page the same ticks expand into the check list with evidence, and the decision sits directly under it. A blocked proposal has no Approve button there, only a sentence naming what stopped it.

## Checked against the generic defaults

- First pass used a mono face for rule ids and evidence. Dropped: that is the common tell. Ids use the grotesk with tabular numbers.
- First pass put each section in a rounded card. Dropped: sections sit on the page, separated by space. Only the gate has a surface, because it is one object.
- No all-caps labels, no eyebrow text, no middle-dot metadata, no arrows on buttons, no gradients.
- Motion only answers an action: expanding a check.
