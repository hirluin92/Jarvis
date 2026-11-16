import pytest

from app.catalog import load_catalog

ONE = """
products:
  - name: Alpha
    urls: [https://alpha.example]
    github_repo: me/alpha
"""

TWO = (
    ONE
    + """
  - name: Beta
    urls: [https://beta.example]
"""
)


def _write(tmp_path, text):
    p = tmp_path / "products.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_select_all_and_by_name_case_insensitive(tmp_path):
    cat = load_catalog(_write(tmp_path, TWO))
    assert [p.name for p in cat.select(None)] == ["Alpha", "Beta"]
    assert [p.name for p in cat.select("beta")] == ["Beta"]


def test_unknown_product_lists_known_ones(tmp_path):
    cat = load_catalog(_write(tmp_path, ONE))
    with pytest.raises(ValueError, match="Known products: Alpha"):
        cat.select("Zeta")


def test_second_product_needs_only_yaml_changes(tmp_path):
    path = _write(tmp_path, ONE)
    assert len(load_catalog(path).products) == 1
    path.write_text(TWO, encoding="utf-8")
    cat = load_catalog(path)
    assert len(cat.products) == 2
    assert cat.products[1].github_repo is None  # optional fields stay optional
