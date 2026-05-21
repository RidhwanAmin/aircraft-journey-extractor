# Aircraft Journey Extractor

Prototype backend service that converts an image of an *Aircraft Journey Summary* form (printed labels + handwritten values, mixed quality) into structured JSON.

## Approach

A vision LLM (`gpt-4o`, temperature 0) is called with the form image and a strict JSON schema. The model returns the populated schema directly. No OCR, no layout detection, no per-cell pipeline.

**Why this over alternatives:**

- **Classical OCR (Tesseract / PaddleOCR)** — fails on the cursive defect block, which is the hardest and most variable part of the form. Even the handwritten cells (`KUL`, `SIN`, `12k`) are unreliable on cursive-tolerant OCR.
- **Specialized handwriting OCR (TrOCR / Google Document AI)** — better on handwriting, but requires a layout/segmentation step on top, and is materially worse on free-form cursive than a frontier VLM.
- **Hybrid layout-detection + per-cell OCR** — most code, most failure modes, wrong scope for "smallest working path."
- **Vision LLM with structured outputs** — one API call, ~50 LOC, handles printed labels and cursive handwriting in a single pass, and OpenAI's strict-schema mode guarantees JSON-shape validity. Trades external API dependency for dramatically less code and better correctness on the hard cases.

**Schema philosophy: permissive strings, no normalization.** All extracted values are nullable strings. `fuel` stays `"12k"`, `load` stays `"150"`. Field names follow the form's labels, not the assessment's prose (the form says "Load", not "Passengers"). Rationale: any normalization is a lossy guess — `"12k"` could be kg or lbs, and "Load" in aviation is not necessarily passenger count. Downstream consumers parse if they need to; we don't fabricate.

**Confidence signal.** Two top-level fields — `needs_review: bool` and `low_confidence_fields: [str]` — let the model surface uncertainty so a downstream maintenance portal can route ambiguous forms to a human reviewer. This is the one piece of "production-aware" design we add because the assessment explicitly flags variable quality.

**Defect message: verbatim only.** Aviation shorthand is safety-critical context. We transcribe what's on the page; we do not interpret, expand, or structure it. Downstream humans interpret.

## Assumptions, scope, constraints

- A single OpenAI API key is available via `OPENAI_API_KEY`.
- One image per request. The maintenance portal's "2–3 forms per flight" is the caller's loop, not the API's batch.
- The form layout shown in `samples/sample1.png` is representative; the prompt does not hard-code field positions, so reasonable layout variation should still work.
- Evaluated on the single provided sample image. Broader validation requires additional photographed forms.
- Production hardening (auth, retries, rate limiting, observability, queueing, async jobs, deployment) is explicitly out of scope per the assessment.

## How to run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# Option 1 — notebook
jupyter notebook demo.ipynb

# Option 2 — API server
uvicorn api:app --reload
# Then:
curl -X POST http://localhost:8000/extract \
  -F "image=@samples/sample1.png"
```

## JSON schema

| Field | Type | Notes |
|---|---|---|
| `aircraft_model` | `str \| null` | e.g. `"Airbus A320"` |
| `registration_number` | `str \| null` | e.g. `"9M-XX1"` |
| `departure_airport` | `str \| null` | as written on the form |
| `arrival_airport` | `str \| null` | as written on the form |
| `crew` | `str \| null` | verbatim |
| `load` | `str \| null` | verbatim (form's label, not normalized to "passengers") |
| `fuel` | `str \| null` | verbatim (`"12k"` stays `"12k"`) |
| `defect_message` | `str \| null` | verbatim free text; no interpretation |
| `needs_review` | `bool` | true if any field is null or ambiguous |
| `low_confidence_fields` | `[str]` | names of fields the model flagged as uncertain |

## Sample output (`samples/sample1.png`)

Below are **two separate runs** of `gpt-4o` (temperature 0) on the *same* image. The structured fields are byte-identical; only `defect_message` drifts:

```json
// Run A
{
  "aircraft_model": "Airbus A320",
  "registration_number": "9M-XX1",
  "departure_airport": "KUL",
  "arrival_airport": "SIN",
  "crew": "4",
  "load": "150",
  "fuel": "12k",
  "defect_message": "un.1 pnl do 744161 and do 744162 ac ess feed control and ac ess feed pw sw found ut to be uncheck operative",
  "needs_review": true,
  "low_confidence_fields": ["defect_message"]
}
```

```json
// Run B (same image, same settings)
{
  "aircraft_model": "Airbus A320",
  "registration_number": "9M-XX1",
  "departure_airport": "KUL",
  "arrival_airport": "SIN",
  "crew": "4",
  "load": "150",
  "fuel": "12k",
  "defect_message": "un.1 panel and rw4161 and do rw4162 nc ess power control and ac ess power by sw panel ut to be checked operative",
  "needs_review": true,
  "low_confidence_fields": ["defect_message"]
}
```

**This divergence is the design working as intended, not a defect.** The seven structured fields (printed labels, hand-printed values) are stable across runs and read exactly. The dense cursive defect block is not reproducible even at `temperature=0` — and that run-to-run instability is *precisely* the signal the model captures by listing `defect_message` in `low_confidence_fields` and setting `needs_review: true`. A downstream maintenance portal uses that flag to route the form to a human reviewer and direct their attention to the defect block. The fields you can trust are stable; the field you can't is flagged.

## API sketch

**Endpoint**

```
POST /extract
Content-Type: multipart/form-data
```

**Request**

| Field | Type | Required | Description |
|---|---|---|---|
| `image` | file | yes | The form image (PNG, JPEG, GIF, or WebP) |

**Response — 200 OK**

`application/json`, shape matches the schema table above.

**Errors**

- `422 Unprocessable Entity` — missing `image` field (FastAPI default).
- `500 Internal Server Error` — upstream OpenAI failure. No retry logic; caller decides.

**Example**

Request:
```
POST /extract HTTP/1.1
Content-Type: multipart/form-data; boundary=...

--...
Content-Disposition: form-data; name="image"; filename="sample1.png"
Content-Type: image/png

<binary>
--...--
```

Response:
```json
{
  "aircraft_model": "Airbus A320",
  "registration_number": "9M-XX1",
  "departure_airport": "KUL",
  "arrival_airport": "SIN",
  "crew": "4",
  "load": "150",
  "fuel": "12k",
  "defect_message": "<verbatim transcription>",
  "needs_review": true,
  "low_confidence_fields": ["defect_message"]
}
```

## Repository layout

```
extractor.py        # extract(image_bytes) -> dict  — the core function
api.py              # FastAPI wrapper, single POST /extract endpoint
demo.ipynb          # notebook: image + extracted JSON side-by-side
samples/sample1.png # provided sample form
requirements.txt
README.md
```

## Out of scope (deliberately)

Auth, rate limiting, retries, async/batched jobs, OCR fallback, IATA code validation, fuel-unit normalization, defect-message structuring, provider abstraction, Docker, observability, automated tests beyond running the notebook.
