# Approach

Design rationale for the Aircraft Journey Extractor. Framed around the assessment's two priorities: **correctness on common cases** and **smart trade-offs**.

---

## 1. The problem, concretely

The input is a photograph of a filled paper form with two visually distinct regions:

| Region | Source | Difficulty |
|---|---|---|
| Field labels (left column) | Printed | Trivial — high-contrast machine print |
| Aircraft model, registration | Printed (pre-filled) or block hand-print | Trivial to easy |
| Airport codes, crew, fuel, load | Hand-printed numerals and 3-letter codes | Moderate — short tokens, mixed printing styles |
| Defect message | Dense cursive shorthand, aviation jargon, abbreviations | Hard — multi-line, low contrast, ambiguous tokens |

Any extractor will be evaluated on whether it gets the *easy and moderate* fields right reliably, and whether it *fails gracefully* on the hard field. Those two together define "correctness on common cases."

## 2. Chosen approach

A single call to a frontier vision LLM (`gpt-4o`, temperature 0) with OpenAI's strict JSON-schema response format. The schema is the contract; the model fills it.

```
image bytes ──► base64 data URI ──► gpt-4o (vision + strict JSON schema) ──► validated dict
```

The entire pipeline is one function (`extract()`), one model call, no preprocessing, no postprocessing. The FastAPI endpoint and the notebook both call this same function.

## 3. Why vision LLM over the alternatives

| Approach | Correctness on clean fields | Correctness on cursive defect | Code size | Verdict |
|---|---|---|---|---|
| Tesseract / PaddleOCR + regex | Moderate (struggles on hand-print) | Effectively zero on cursive | Medium | Wrong — fails on the hardest field |
| TrOCR + line segmentation | Good on hand-print | Weak on free-form cursive | Large (multi-stage pipeline) | Wrong scope for "smallest working path" |
| Google Document AI / AWS Textract | Strong on forms | Decent on handwriting | Small | Reasonable, but a vendor lock-in with no edge over a VLM |
| Layout detection + per-cell OCR | Variable | Variable | Largest | Wrong scope — engineering hours don't buy correctness here |
| **Vision LLM + strict JSON schema** | **Strong** | **Best available; gracefully degrades** | **~50 LOC** | **Chosen** |

The decisive factor: **the defect message is the failure point of every other approach**. A frontier VLM is the only off-the-shelf option that reads cursive aviation shorthand at all, and OpenAI's strict-schema mode guarantees the output is shape-valid JSON without parsing or validation code.

## 4. Correctness on common cases

The "common case" is a legibly-filled form like `samples/sample1.png`: printed labels, hand-printed values for the structured fields, cursive in the defect block. On this case the approach produces:

| Field | Result on the sample | Why this is the common case |
|---|---|---|
| `aircraft_model` | `"Airbus A320"` — exact | Printed field, always machine-readable |
| `registration_number` | `"9M-XX1"` — exact | Usually printed or block-printed |
| `departure_airport`, `arrival_airport` | `"KUL"`, `"SIN"` — exact | Short IATA-style tokens, conventionally hand-printed |
| `crew` | `"4"` — exact | Single digit or two digits, always |
| `load` | `"150"` — exact | Small integer |
| `fuel` | `"12k"` — exact, including the unit suffix | Conventional shorthand, kept verbatim |
| `defect_message` | Best-effort cursive transcription, flagged for review | Always the hard field; flagged correctly |
| `needs_review` | `true` | Correctly fires because of the defect block |
| `low_confidence_fields` | `["defect_message"]` | Correctly identifies the only ambiguous field |

Seven of the eight content fields are exact. The eighth (defect message) is correctly self-identified as needing human review. This is exactly the behavior a downstream maintenance portal needs: high-confidence structured data on the clean fields, a flag on the noisy one.

## 5. Smart trade-offs (each one is a deliberate "no")

Each decision below is a place where we *chose not to do something*. The rationale follows the trade.

### 5.1 No normalization — values are kept as written
`fuel` stays `"12k"`. `load` stays `"150"`. Airport codes are not validated against an IATA list.
- **Win:** zero fabricated data. `"12k"` could be 12,000 kg or 12,000 lbs — the form doesn't say. Pretending we know is a correctness failure waiting to happen downstream.
- **Cost:** consumers parse if they need numerics. One line of code per consumer.
- **Why this is a smart trade:** lossy normalization is a *correctness regression* dressed up as a feature. Downstream is the right place to apply domain rules.

### 5.2 Field names follow the form, not the assessment prose
The form's left column says "Load". The assessment text says "passenger and crew count". Our schema field is `load`, not `passengers`.
- **Win:** the JSON describes what is actually on the page. "Load" in aviation does not always mean passenger count (it can mean payload weight).
- **Cost:** a downstream consumer who expected `passengers` has to rename one field.
- **Why this is a smart trade:** the form is the source of truth, not the spec's illustrative prose. Quietly relabeling fields is the kind of "smart guess" that causes incidents.

### 5.3 All fields are nullable strings
Every value is `str | null`. No ints. No enums.
- **Win:** the schema can represent any value the form has, including partial reads ("~150", "12-13k"), without the model being forced into a type lie.
- **Cost:** stronger types would let downstream skip a parse step.
- **Why this is a smart trade:** the assessment explicitly notes "fields may be missing or ambiguous." A typed schema forces the model to either fabricate a number or fail — both are correctness regressions. Strings + `null` honestly represent ambiguity.

### 5.4 Confidence signal is two fields, not a per-field score
`needs_review: bool` and `low_confidence_fields: [str]`. No per-field probability.
- **Win:** machine-actionable ("route to human if `needs_review`") without inviting false precision.
- **Cost:** a portal cannot rank fields by confidence within a flagged form.
- **Why this is a smart trade:** VLM-reported per-field confidences are notoriously poorly calibrated. A coarser, well-defined signal is more reliable than a finer, noisier one.

### 5.5 Defect message is verbatim, never interpreted
We do not expand `DD 244161` to "Deferred Defect 244161". We do not parse out DD numbers as a list. We do not split into structured sub-fields.
- **Win:** no fabricated semantics on safety-relevant text.
- **Cost:** downstream cannot query "all forms referencing DD 244161" without its own parser.
- **Why this is a smart trade:** aviation maintenance shorthand is dialectal and high-stakes. A wrong interpretation by the extractor is worse than an honest transcription that a human or a dedicated domain parser handles downstream.

### 5.6 No retries, no fallbacks, no provider abstraction
A single `OpenAI()` client, one model name in a constant, no exception handling beyond what FastAPI does by default.
- **Win:** ~50 LOC total. Every line traces to a requirement.
- **Cost:** an OpenAI outage is a 500 to the caller.
- **Why this is a smart trade:** production hardening is an explicit non-goal. Adding retry/fallback/provider plumbing *before* you have throughput requirements is the textbook YAGNI overshoot the assessment is testing for.

### 5.7 Sync single-image API, not batch or async
`POST /extract` with one image, returns one JSON.
- **Win:** simplest possible contract. Caller writes a `for` loop if they need multiple.
- **Cost:** no batching efficiency at the API layer.
- **Why this is a smart trade:** the assessment mentions 2–3 forms per flight. That volume does not justify async-job machinery or batch endpoints. Make the simple thing simple; the caller's `for` loop is already correct.

### 5.8 One sample image, honest about it
The README does not invent synthetic samples. It says, in writing, that the approach was evaluated on one form.
- **Win:** the reviewer knows exactly what was tested.
- **Cost:** less surface area to claim "robustness" on.
- **Why this is a smart trade:** synthetic samples lifted from the same source image would be self-confirming. Honesty about the evaluation set is more useful than a manufactured robustness story.

## 6. How `needs_review` and `low_confidence_fields` are determined

These two fields are **populated by the model itself**, not by separate calibration code. There is no probability threshold, no per-token log-prob inspection, no ensemble vote, no secondary classifier. The mechanism has three parts:

1. **The prompt instructs the model** to flag uncertainty.
2. **The strict JSON schema forces both fields to exist** in every response. The model literally cannot return a result that omits them.
3. **The model uses its own judgment**, formed during the same forward pass as the transcription, to decide which fields belong in `low_confidence_fields` and whether `needs_review` should be `true`.

### The instruction the model is following

From the system prompt in `extractor.py`:

> If a field is missing, illegible, or you are uncertain, set its value to null and add the field name to low_confidence_fields. Set needs_review to true if any field is null or you have any doubt about the transcription.

That sentence is the entire mechanism. It does two things:

- Sets the **threshold**: "any doubt" — deliberately permissive, biased toward over-flagging rather than under-flagging.
- Sets the **coupling**: `low_confidence_fields` lists the *which*; `needs_review` is the *whether-any*. The model is asked to keep them consistent (any entry in the list → flag is true), but the schema does not enforce that — it is a contract the prompt asks the model to maintain.

### What "uncertainty" effectively means here

For a VLM, this is not a calibrated probability. It is the model's *self-assessment*, formed jointly with the transcription. In practice it tracks:

- **Visual cues** — low contrast, smudges, occluded characters, cursive that does not decompose cleanly into letters.
- **Linguistic plausibility** — tokens that violate the form's conventions (an "airport code" that is not three letters; a "crew" value with non-digits).
- **Self-consistency** — places where two plausible transcriptions exist and the model would have to pick one.

There is no separate "confidence head" producing a score. The model decides what to write and how sure it is in one pass.

### Observed behavior on the sample

For `samples/sample1.png` the model returned:

```json
"needs_review": true,
"low_confidence_fields": ["defect_message"]
```

Correctly: the seven structured fields are clean (printed labels, hand-printed values) and read exactly; the defect block is dense cursive shorthand and is the only field flagged. The pair is directly actionable for a downstream maintenance portal: route this form to a reviewer, and direct their attention to the defect block.

### Honest limits of this mechanism

VLM self-reported confidence is **not well calibrated**. Two failure modes to expect:

- **False negative — confident misread.** The model reads `"0"` as `"O"` (or `"1"` as `"l"`) and reports it confidently. `low_confidence_fields` stays empty for that field even though the value is wrong. The portal will not know to look.
- **False positive — over-cautious flag.** The model flags a perfectly legible field because of unfamiliar context (an unusual aircraft model, a non-IATA airport code). The portal queues a form for human review unnecessarily.

The prompt deliberately biases toward **false positives** by using "any doubt" as the threshold. Rationale: a human reviewer can dismiss an unnecessary flag in seconds; a silent misread is uncatchable downstream. Over-flagging is the cheaper error.

### What would harden this further (deliberately not built)

If the signal had to be tighter for production use, the next moves would be:

- **Two-pass verification.** Call the VLM a second time with the extracted JSON and the image: "are these values consistent with what's on the form?" Treat disagreement as low-confidence. Doubles cost and latency.
- **Token-level log-probs.** OpenAI exposes `logprobs` on some chat-completions endpoints; surfacing them per field would give a calibrated character-level signal. Adds complexity and may not compose cleanly with strict-schema responses across model versions.
- **Cross-field plausibility rules.** `departure_airport != arrival_airport`, `crew` is a positive integer, IATA codes are three uppercase letters. Catches structured errors cheaply but adds aviation domain rules the assessment scope excludes.
- **Model ensemble.** Run two independent VLMs (e.g. `gpt-4o` and `claude-sonnet-4.5`), mark any disagreement as low-confidence. Most reliable, most expensive, doubles vendor surface area.

For a prototype, single-model self-report is the right floor: zero extra code, useful signal in the common case, no false-precision dressing. Each of the harder mechanisms above is a production-hardening concern, which the assessment explicitly takes off the table.

## 7. Where this approach will degrade

Known failure modes, kept short and unvarnished:

- **Heavy cursive on the structured fields** (e.g. a writer scribbles "Airbus" in unreadable script). The VLM may produce a guess; the `needs_review` flag should fire, but per-field confidence is the model's call and can miss.
- **Forms with different layouts.** The prompt does not pin field positions, so reasonable layout variation should work, but a radically different form (different labels, different language) will degrade silently. A schema versioning field would address this later.
- **Adversarial or low-quality scans** (heavy skew, partial cropping, very low resolution). No preprocessing means whatever the VLM sees is what gets read. A pre-resize/auto-rotate step would help; it was traded away for simplicity.
- **OpenAI API unavailable.** No retry, no offline fallback. The caller sees a 500.

Each of these is a known and accepted limit for a prototype. The next-iteration moves — preprocessing step, layout-detector front-end, retry/backoff, optional self-hosted VLM — are documented but deliberately not built.
