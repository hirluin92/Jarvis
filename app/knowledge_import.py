"""`uv run python -m app.knowledge_import [folder]`: import knowledge/<product>/*.md."""

import asyncio
import sys
from pathlib import Path

from app.catalog import get_catalog
from app.config import get_settings
from app.db import session as db
from app.knowledge import import_dir


async def main(root: Path) -> int:
    if not root.is_dir():
        print(f"Cartella non trovata: {root}")
        return 1
    db.init_db(get_settings().database_url)
    counts = await import_dir(root)
    known = {p.name.lower() for p in get_catalog().products}
    for product, n in counts.items():
        flag = "" if product.lower() in known else "  (ATTENZIONE: non è nel catalogo)"
        print(f"{product}: {n} sezioni importate{flag}")
    if not counts:
        print("Nessun documento .md trovato.")
    return 0


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("knowledge")
    sys.exit(asyncio.run(main(folder)))
