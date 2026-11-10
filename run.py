"""Start clippie (barebone): python run.py  ->  http://127.0.0.1:8100"""
import uvicorn

if __name__ == "__main__":
    # Bound to localhost on purpose: the prototype has no authentication yet.
    uvicorn.run("clippie.api:app", host="127.0.0.1", port=8100)
