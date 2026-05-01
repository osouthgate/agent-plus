"""Sample subcommand: overview."""
TOOL_NAME = "sample"
import json
def main():
    print(json.dumps({"tool": {"name": "sample", "version": "0.0.1"}, "ok": True}))
