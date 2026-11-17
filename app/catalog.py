from pathlib import Path

import yaml
from pydantic import BaseModel

from app.config import get_settings


class Product(BaseModel):
    name: str
    urls: list[str] = []
    github_repo: str | None = None
    deploy_workflow: str | None = None  # workflow file (e.g. deploy.yml) that trigger_deploy runs
    deploy_ref: str = "main"


class Catalog(BaseModel):
    products: list[Product] = []

    def select(self, name: str | None) -> list[Product]:
        """All products when name is None, otherwise the one matching name (case-insensitive)."""
        if name is None:
            return list(self.products)
        found = [p for p in self.products if p.name.lower() == name.lower()]
        if not found:
            known = ", ".join(p.name for p in self.products) or "(none)"
            raise ValueError(f"Unknown product: {name!r}. Known products: {known}")
        return found


def load_catalog(path: str | Path) -> Catalog:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Catalog.model_validate(data)


def get_catalog() -> Catalog:
    return load_catalog(get_settings().products_file)
