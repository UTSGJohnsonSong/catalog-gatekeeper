# UI design

Since 2026-11-04 the frontend has two pages. The owner chose both on 2026-11-04: the review UI moves to Shopify's
Polaris components, and a landing page is added, built from the plan below that the owner had seen first.

| Page | Path | Built with | Entry |
|---|---|---|---|
| Landing page | `/` | Its own CSS: Apple-like type and neutrals, the project's accent colours | `frontend/index.html`, `frontend/src/landing/` |
| Review UI | `/app` and below | Shopify's Polaris web components, plus a few parts of our own | `frontend/app/index.html`, `frontend/src/main.tsx` |

They are separate pages because only the review UI loads Polaris from `cdn.shopify.com`. The API server sends each
its own Content-Security-Policy (`src/gatekeeper/api/app.py`): the landing page and the API allow no other origin.

## Landing page

On 2026-11-23 the page grew into one long product page, built from a design the owner approved that day. On
2026-11-26 it was rebuilt from a second plan the owner approved, after the feedback that it had too many words and
nothing to operate. It went from about 2,500 words on some 15 screens to about 830 words at rest on six or seven
screens at 1280px wide, counted as the browser's `innerText` of `main`. The Apple-like restyle later that day kept
the words and opened up the spacing, so the page now runs to about ten screens of 1280x800.

**Job.** A recruiter or reviewer understands in 30 seconds what this is and why to trust it. Whoever reads on finds
every part of the product explained, then opens the demo.

- One idea to a screen. Each section is one figure the visitor can operate, with a line or two of text under it. A
  longer explanation sits behind the figure it belongs to: a chosen tab, a row's popover or a native `details`.
- What the page must admit stays in view whatever is open or closed: the store is simulated, a template script
  drafts the demo's fixes, the eval data is synthetic and shares an author with the rules, the hosted LLM ran once
  on 40 synthetic products, the adapter has never run against a merchant's store, and embedded mode has not yet
  run against Shopify itself. A test strips everything hidden and checks that these sentences are still there.
- Look. On 2026-11-26 the owner asked for the feel of an Apple product page, from a plan with measured numbers.
  The numbers below are that plan's.
- Type: one sans family, `-apple-system, BlinkMacSystemFont, "Inter Variable", "Segoe UI", Roboto, sans-serif`.
  Apple devices show their own system face, SF Pro, which is not bundled because its licence forbids that. Every
  other device shows Inter, bundled from `@fontsource-variable/inter` in its optical-size build, so large headlines
  get the display cut. Two weights, 400 and 600. Newsreader and Familjen Grotesk were removed, and with them the
  split between the catalog's voice and the system's. Only ids and figures use tabular numbers, because in Inter
  the same feature also widens the hyphen in running text.
- Type scale, desktop / tablet / phone (breakpoints 1068px and 734px): the headline 64 / 56 / 40px at line-height
  1.06; section headlines 56 / 48 / 32px; subheads and panel titles 24 / 24 / 21px; ledes 21 / 21 / 19px in the
  secondary grey; body 17px at line-height 1.47; captions and small print 14px. Nothing is smaller than 12px.
- Neutrals, light and dark: page `#FFFFFF` / `#000000`, band `#F5F5F7` / `#161617`, ink `#1D1D1F` / `#F5F5F7`,
  secondary text `#6E6E73` / `#A1A1A6`, hairline `#D2D2D7` / `#424245`, faint line `#E8E8ED` / `#2C2C2E`. A card
  takes the other surface of its section: `#F5F5F7` on a white section and `#FFFFFF` on a band (dark `#161617`
  and `#1D1D1F`). The secondary grey keeps 4.5:1 on all of them (4.66:1 at its lowest, on `#F5F5F7`).
- Colour: harbour blue and the check colours from the table below are unchanged, with no new hues. Two more tokens,
  `--meter` and `--meter-stopped`, colour only the bar fills of the numbers section: `#24508F` and `#AE2640` in
  light, `#5F93E4` and `#E5607A` in dark, because the dark theme's blue and red are too light to work as bars. The
  page's Content-Security-Policy allows no other origin and no inline style, so it loads no outside fonts, scripts
  or images, and every drawing is coloured by classes.
- Spacing and surfaces: the content is min(1100px, 87.5%) wide and centred, with paragraphs of at most 40rem.
  Sections have 140 / 100 / 72px above and below, and 56 / 48 / 32px from a headline to its figure. Only
  alternating bands separate them, white and grey, starting with the hero on white. Cards have 28px corners (20px
  on a phone) and no border or shadow; buttons and the gate's four choices are pills. The masthead is a 52px bar
  that stays at the top, translucent over a blur, the page's one effect, and solid where the browser cannot blur
  or the visitor asks for less transparency. Below 1068px the seven section links do not fit beside the name and
  the demo pill, so they get a row of their own under the bar that scrolls away with the page.
- Layout: the hero is centred, with the gate card under it; on desktop the card shows the draft and the four
  choices side by side, so the choices are on the first screen at 1280x800. The other sections are left-aligned.
  From 1068px a section can split into a copy column of 31rem and a figure column.
- Sections, in order: the hero and its gate; "One product, seven steps" (seven tabs on one lane, one snapshot at a
  time, with Previous and Next); "What it looks for" (A01 to A13 as tiles, each opening a real example from the
  simulated store; V01 to V12 as a list that opens); "What it can never do" (a drawing of the moves a fix can make,
  beside the hard limits as rows that open); "Measured, and not measured" (a meter list, under a lede that says
  what the figures do not show); "Inside" (the drawing and the stack); "Status"; "Store data"; the way into the
  demo; the footer. The masthead links to each section.
- Signature: the gate in the hero. The visitor tries to get a bad fix past it: four buttons add a link, an invented
  warranty, a made-up number or a hidden instruction to the four lines the demo's template drafts for one product,
  before the link it adds on purpose. The 12 checks light in
  order and the verdict names each check that stopped the fix and the words it found, which are marked in the line.
  The page decides nothing: `scripts/build_page_samples.py` ran the project's validators on all 16 combinations and
  saved their answers in `gate.json`. Each addition keeps a blank ruled line until it is chosen, so choosing one
  never moves the buttons.
- The gate run stays the only motion that plays by itself. It plays once on load and once for each change, about a
  second. With reduced motion the finished state shows at once. The verdict is the page's one live region and is
  filled only when a run has finished, so a screen reader hears each result once. On a phone the 12 checks become
  two rows of six.
- Choosing follows one pattern, the ARIA tabs pattern with automatic activation (`tabs.ts`): the seven steps, the
  rule tiles, the states of a fix and the boxes of the architecture drawing. The meter rows are a table, so each
  row's name is a button that pins its explanation; pointing at a row or focusing its name shows it as well.
- The meter list has one row for each figure: a name, a bar and the figure as text. Every bar starts on one edge and
  runs on one scale, from nothing to that row's whole. One hue; the stopped colour is kept for A13, the one measure
  below its floor, which also carries an icon, the words and a tick at the floor. Rows where lower is better say so.
- The two drawings are inline SVG. The architecture is drawn from `docs/ARCHITECTURE.md` at its natural size, so no
  label is under 12px; on a narrow screen it scrolls sideways inside its own frame, never the page. A box says
  little more than its name, and choosing one puts what it does on the line under the drawing. The heavier outlines
  mark the two places that decide.
- Sources. The fixes are copied from a demo run into `frontend/src/landing/sample.json`. The store's copy of the
  product is copied from `fixtures/shopify/catalog_demo.json` into `frontend/src/landing/product.json`. The gate's
  answers and one example for each audit rule are written by `scripts/build_page_samples.py` into `gate.json` and
  `rules.json`; they are never edited by hand. The figures are copied from the eval reports named in `Landing.tsx`,
  the failing rule A13 included. The copy for the rules, limits, moves, figures and status is in
  `frontend/src/landing/content.ts`, which names the documents it follows; the step captions are in `Steps.tsx`
  and the drawing's texts in `Architecture.tsx`. Tests pin the copies but do not read the sources, because the Docker build sees only `frontend/`, so
  change both together.
- Checked against the generic defaults: no all-caps eyebrows, no middle-dot meta strings, no arrows on buttons, no
  rows of identical shadowed cards, no gradients, no emoji, and numbers only on the seven steps.

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

Harbour blue and the check colours below still colour the landing page. Its neutrals and its type were replaced on
2026-11-26, as the Landing page section says.

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
