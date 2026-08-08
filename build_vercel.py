"""Vercel build step (see pyproject.toml's [tool.vercel.scripts]).

Vercel serves static assets from public/**, not from a Flask app's own static
folder — but the app's templates already reference /static/... via Flask's
url_for('static', ...), and local dev still needs static/ to work as-is. So
rather than keeping two copies in the repo, this mirrors static/ into
public/static/ at build time; static/ stays the one source of truth.
"""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parent
shutil.copytree(root / "static", root / "public" / "static", dirs_exist_ok=True)
print("Mirrored static/ -> public/static/ for Vercel's CDN.")
