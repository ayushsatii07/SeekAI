"""
SeekAI CLI entry point.

This file is the entry point referenced in pyproject.toml:
    [project.scripts]
    seekai = "cli:app"

Usage:
    seekai search "query"
    seekai ask "query"
    seekai history
    seekai show <id>
    seekai local-search "query"
    seekai stats
"""
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.cli.commands import app

if __name__ == "__main__":
    app()
