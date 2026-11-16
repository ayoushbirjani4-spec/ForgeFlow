# Creator workflow

Task IDs: `CRE-001`, `CRE-002`, …

## 1. Brief (`templates/content-brief.md`)
Topic, audience, platform, format, purpose, tone, length, required sources.
Ask only essential questions.

## 2. Research and evidence
- Prefer primary sources. Generic search queries only (shared rule 5).
- One evidence file per source/claim pair:
  `.forgeflow/evidence/EV-00N.json` (schema `evidence.schema.json`), with
  the URL, title, publisher, published date if known, `retrieved_at`, and an
  excerpt of **300 characters or fewer**, which is enough to verify against
  without reproducing the work.
- Mark `supports`, `partial` or `contradicts`. Contradicting evidence is kept.
- Never record a source you could not actually open.

## 3. Strategy
Audience, angle, key messages, outline, and an **evidence map** in which every
factual claim gets an `EV-` ID or an "opinion" label.

## 4. Approval
`FF propose --action implementation --files ".forgeflow/artifacts/**"` (plus
any project paths the content goes into).

## 5. Production
Follow the approved brief and evidence map. A claim with no `EV-` ID is either
removed or flagged; it is never written in as fact. Direct quotes stay short
and are attributed.

## 6. Validation
The validator checks each claim against the **saved** evidence excerpt (it
re-fetches only when the excerpt is insufficient, noting the date), then checks
quotes and attribution, misleading framing, brief compliance and format.

## 7. Optimization
SEO, readability and platform variants. Optimization never adds claims; any
change it makes is re-validated.

## 8. Delivery
Final content, source list, fact-check report, platform variants and a
publishing checklist. Publishing or sending needs its own `publish` approval.
