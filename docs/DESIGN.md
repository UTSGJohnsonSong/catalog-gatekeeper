# UI design

Since 2026-11-04 the frontend has two pages. The owner chose both on 2026-11-04: the review UI moves to Shopify's
Polaris components, and a landing page is added, built from the plan below that the owner had seen first.

| Page | Path | Built with | Entry |
|---|---|---|---|
| Landing page | `/` | The project's own palette and type | `frontend/index.html`, `frontend/src/landing/` |
| Review UI | `/app` and below | Shopify's Polaris web components, plus a few parts of our own | `frontend/app/index.html`, `frontend/src/main.tsx` |

They are separate pages because only the review UI loads Polaris from `cdn.shopify.com`. The API server sends each
its own Content-Security-Policy (`src/gatekeeper/api/app.py`): the landing page and the API allow no other origin.

## Landing page

**Job.** A recruiter or reviewer understands in 30 seconds what this is and why to trust it, then opens the demo.

- Palette and type: the table and the two type voices below, light and dark.
- Layout: one left-aligned column of about 68 characters. On wide screens the docket panel reaches right of it.
  Four parts: a real fix before and after, the seven steps a fix moves through (numbered, because they are a
  sequence), the measured numbers with their limits, and the way into the demo.
- Signature: the gate run. On load, one real fix from the demo lights the 12 checks in order and stops at "Waiting
  for a person to approve". It runs once. With reduced motion the finished state shows at once, and screen readers
  get the result as one sentence. On a phone the 12 checks become a 4 by 3 grid.
- The fixes shown are copied from a demo run into `frontend/src/landing/sample.json`, which says where they came
  from. The figures are copied from the eval reports named in `Landing.tsx`, the failing rule A13 included. A test
  pins the copies but does not read the reports, so change both together.

## Review UI on Polaris

- Polaris draws the page frame, sections, tables, badges, banners, buttons and fields. Outside the Shopify admin
  its page header shows the breadcrumb on the left and the page action on the right; on a phone the breadcrumb folds
  into a menu.
- Ours, because Polaris has no equivalent: the top navigation bar, the 12-tick gate strip and check list, the word
  diff, and the citations. They use the same font stack as Polaris and the check colours from the palette below, so
  the ticks a visitor first sees on the landing page look the same here.
- The status filter is a row of pills, a filled one for the current view, as in the Shopify admin. Polaris has no
  tabs component.
- Light only. Polaris documents no dark mode, so the review UI declares `color-scheme: light` rather than mixing
  a dark page with light components.
- If the Polaris script does not arrive, the page says so instead of showing empty shells.

## Review UI plan of 2026-10-31 (superseded on 2026-11-04, kept for the record)

The palette and type below still define the landing page.

The owner's rule is to see a design plan before building. The owner is unavailable until after 2026-11-03 and delegated this build, so the plan was decided here and can be changed afterwards.

**Subject and job.** A merchant or catalog manager decides whether a proposed product-text change goes live. The page must make it obvious what would change, what checked it, and why a blocked fix cannot be approved.

### Palette

| Name | Light | Dark | Used for |
|---|---|---|---|
| Inspection paper | `#EDF1F3` | `#151C29` | Page background |
| Docket ink | `#17243B` | `#E4EAF2` | Text |
| Harbour blue | `#24508F` | `#8DB2EA` | Actions, links |
| Cleared green | `#1B7354` | `#58C39A` | Passed checks, added text |
| Stopped red | `#AE2640` | `#F08595` | Blocking failures, removed text |
| Hold ochre | `#94600A` | `#E0B060` | Warnings, risk |

### Type

- Familjen Grotesk for the system's voice: navigation, labels, numbers, buttons.
- Newsreader for the catalog's voice: anything that came from a product or a policy document.
- The split carries meaning. The reader can always tell who is speaking, the tool or the store. Both are bundled with the app, so no request leaves for a font CDN.

### Layout

```
Queue                                  Proposal
+--------------------------------+     +----------------------+----------+
| Title        gate strip   risk |     | Before / after       | The gate |
| Title        gate strip   risk |     | per changed field    | 12 checks|
| Title        gate strip   risk |     | Citations            | Approve  |
+--------------------------------+     +----------------------+----------+
```

Left aligned throughout. On a phone the gate stacks under the diff, and the decision bar stays at the end of the gate.

### Signature element: the gate

Every proposal passes the same 12 deterministic checks. The gate strip draws them as 12 ticks in a fixed order, the same on the queue row and the proposal page, so a reviewer learns the pattern: a taller red tick is a stopped check, and its position says which one. On the proposal page the same ticks expand into the check list with evidence, and the decision sits directly under it. A blocked proposal has no Approve button there, only a sentence naming what stopped it.

### Checked against the generic defaults

- First pass used a mono face for rule ids and evidence. Dropped: that is the common tell. Ids use the grotesk with tabular numbers.
- First pass put each section in a rounded card. Dropped: sections sit on the page, separated by space. Only the gate has a surface, because it is one object.
- No all-caps labels, no eyebrow text, no middle-dot metadata, no arrows on buttons, no gradients.
- Motion only answers an action: expanding a check.
