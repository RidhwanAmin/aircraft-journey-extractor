"""Aircraft Journey Summary extractor.

Single function: send a form image to a vision LLM with a strict JSON schema,
return the parsed fields as a dict.
"""
from __future__ import annotations

import base64

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "gpt-4o"

SYSTEM_PROMPT = """You extract fields from an 'Aircraft Journey Summary' form
used in aircraft maintenance after a flight. The form has printed field labels
on the left and handwritten values on the right.

Rules:
- Transcribe handwritten values verbatim. Do not normalize, expand abbreviations,
  or convert units. Keep "12k" as "12k". Keep "150" as "150".
- The defect message is free-text aviation shorthand. Transcribe what you see
  as closely as possible. Do not interpret or expand maintenance abbreviations.
- If a field is missing, illegible, or you are uncertain, set its value to null
  and add the field name to low_confidence_fields.
- Set needs_review to true if any field is null or you have any doubt about
  the transcription."""


class AircraftJourneySummary(BaseModel):
    aircraft_model: str | None = Field(description="Aircraft model, e.g. 'Airbus A320'")
    registration_number: str | None = Field(description="Registration number, e.g. '9M-XX1'")
    departure_airport: str | None = Field(description="Departure airport code as written")
    arrival_airport: str | None = Field(description="Arrival airport code as written")
    crew: str | None = Field(description="Crew count, verbatim from the form")
    load: str | None = Field(description="Load value, verbatim from the form")
    fuel: str | None = Field(description="Fuel on board, verbatim from the form")
    defect_message: str | None = Field(description="Defect message, verbatim free text")
    needs_review: bool = Field(description="True if any field is null or ambiguous")
    low_confidence_fields: list[str] = Field(description="Names of fields the model was uncertain about")


def _detect_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def extract(image_bytes: bytes) -> dict:
    """Extract Aircraft Journey Summary fields from a form image.

    Requires OPENAI_API_KEY in the environment.
    """
    client = OpenAI()
    mime = _detect_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"

    completion = client.beta.chat.completions.parse(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        response_format=AircraftJourneySummary,
    )
    return completion.choices[0].message.parsed.model_dump()
