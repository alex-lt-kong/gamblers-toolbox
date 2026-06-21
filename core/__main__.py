"""`python -m core --config <path>` — run the unified app with uvicorn."""

import argparse
import os

parser = argparse.ArgumentParser(prog="python -m core")
parser.add_argument("--config", required=True, help="path to the host config TOML")
args = parser.parse_args()

# Propagate so core.main.create_app loads the same config when uvicorn calls it.
os.environ["MARKET_UTILS_CONFIG"] = args.config

import uvicorn

from core import config

cfg = config.load_config()
uvicorn.run("core.main:create_app", factory=True, host=cfg.host, port=cfg.port)
