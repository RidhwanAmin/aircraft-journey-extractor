"""FastAPI wrapper for the Aircraft Journey extractor.

Run: uvicorn api:app --reload
"""
from fastapi import FastAPI, File, UploadFile

from extractor import extract

app = FastAPI(title="Aircraft Journey Extractor")


@app.post("/extract")
async def extract_endpoint(image: UploadFile = File(...)) -> dict:
    image_bytes = await image.read()
    return extract(image_bytes)
