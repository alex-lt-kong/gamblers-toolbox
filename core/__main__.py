"""`python -m core` — run the unified app with uvicorn."""

import uvicorn

from core import config

if __name__ == "__main__":
    cfg = config.load_config()
    uvicorn.run("core.main:app", host=cfg["host"], port=cfg["port"])
