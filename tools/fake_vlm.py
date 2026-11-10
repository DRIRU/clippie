"""A stand-in for an OpenAI-compatible vision endpoint, for trying the AI verification flow
without a GPU. It does NOT look at the images: every reply is canned.

    python -m tools.fake_vlm            # serves http://127.0.0.1:8009/v1
    CLIPPIE_VLM_BASE_URL=http://127.0.0.1:8009/v1 python run.py
"""
import itertools
import json

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
_replies = itertools.cycle([
    {"event": "multi_kill", "is_highlight": True, "confidence": 0.86, "evidence": "Several enemy eliminations appear in the kill feed."},
    {"event": "none", "is_highlight": False, "confidence": 0.72, "evidence": "Frames show a quiet scene with no combat."},
    {"event": "other", "is_highlight": None, "confidence": 0.3, "evidence": "Frames are too dark to tell."},
])


@app.get("/v1/models")
def models():
    return {"data": [{"id": "fake-qwen2.5-vl"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    await req.json()
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(next(_replies))}}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8009)
