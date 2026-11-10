"""Isolate tests from the developer's local .env (real data dir, real VLM endpoint).

Environment variables take priority over .env, so pin them before anything imports clippie.
"""
import os
import tempfile

os.environ["CLIPPIE_DATA_DIR"] = tempfile.mkdtemp(prefix="clippie-test-")
os.environ["CLIPPIE_VLM_BASE_URL"] = ""   # empty => AI verification disabled unless a test mocks it
os.environ["CLIPPIE_VLM_MODEL"] = ""
os.environ["CLIPPIE_AUTO_VERIFY"] = "true"
