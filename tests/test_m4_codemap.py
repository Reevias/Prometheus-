import json
import sys

import pytest

from prometheus.repos.profile import RepoProfile


def profile(folder, name="example", own=True):
    return RepoProfile("owner/" + name, "a" * 40, "", str(folder), "clean", own=own)


def symbol(name, line):
    return {"name": name, "line": line}


def test_python(tmp_path):
    from prometheus.repos.codemap import map_file

    path = tmp_path / "sample.py"
    path.write_text("import os, sys as system\nfrom pathlib import Path\n"
                    "def first(): pass\nasync def second(): pass\n"
                    "class Box:\n    def open(self): pass\n"
                    "raise RuntimeError('never execute this')\n", encoding="utf-8")
    result = map_file(path)
    assert result["language"] == "Python"
    assert result["functions"] == [symbol("first", 3), symbol("second", 4)]
    assert result["classes"] == [dict(symbol("Box", 5), methods=[symbol("open", 6)])]
    assert result["imports"] == ["os", "sys", "pathlib"]


@pytest.mark.parametrize("ext,grammar", [("js", "javascript"), ("ts", "typescript")])
def test_script(tmp_path, ext, grammar):
    pytest.importorskip("tree_sitter_" + grammar, reason="JavaScript/TypeScript grammar unavailable")
    from prometheus.repos.codemap import map_file

    path = tmp_path / ("example." + ext)
    path.write_text("import thing from 'lib';\nexport function run() {}\n"
                    "class Box {\n  open() {}\n}\n", encoding="utf-8")
    result = map_file(path)
    assert result["functions"] == [symbol("run", 2)]
    assert result["classes"] == [dict(symbol("Box", 3), methods=[symbol("open", 4)])]
    assert result["imports"] == ["lib"]
    assert "note" not in result


@pytest.mark.parametrize("ext,source,package", [
    ("rs", "use lib::Thing;\nstruct Box {}\nimpl Box {\n fn open(&self) {}\n}", "lib::Thing"),
    ("go", 'package test\nimport "lib"\ntype Box struct {}\nfunc (b Box) open() {}', "lib"),
    ("java", "import lib.Thing;\nclass Box {\n void open() {}\n}", "lib.Thing"),
    ("cs", "using Lib;\nclass Box {\n void open() {}\n}", "Lib"),
])
def test_native_grammar(tmp_path, ext, source, package):
    from prometheus.repos.codemap import map_file

    path = tmp_path / ("example." + ext)
    path.write_text(source, encoding="utf-8")
    result = map_file(path)
    assert result["classes"][0]["name"] == "Box"
    assert result["classes"][0]["methods"] == [symbol("open", source[:source.index("open(")].count("\n") + 1)]
    assert result["imports"] == [package]


def test_size_and_sample(tmp_path):
    from prometheus.repos.codemap import map_repo

    (tmp_path / "big.py").write_bytes(b" " * 1_000_001)
    assert map_repo(tmp_path, profile(tmp_path))["files"] == []
    for number in range(2001):
        (tmp_path / f"{number}.py").touch()
    result = map_repo(tmp_path, profile(tmp_path))
    assert result["sampled"]
    assert len(result["files"]) == 2000
    assert "big.py" not in {item["path"] for item in result["files"]}
    assert json.loads((tmp_path / "codemap.json").read_text(encoding="utf-8")) == result


def test_lookup(tmp_path):
    from prometheus.actions.atomic_fs import find_symbol
    from prometheus.repos.codemap import map_repo

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "app.py").write_text("\ndef run(): pass", encoding="utf-8")
    result = map_repo(tmp_path, profile(tmp_path), limit_files=1)
    assert not result["sampled"]
    assert find_symbol(tmp_path, "run") == [{"path": "src/app.py", "line": 2}]
    assert find_symbol(tmp_path, "missing") == []


def test_links(tmp_path):
    from prometheus.repos.codemap import map_repo, link_repos

    owner, library, third = [profile(tmp_path / n, n, n != "external")
                             for n in ("app", "library", "external")]
    for folder in ("app", "library", "external"):
        (tmp_path / folder).mkdir()
    (tmp_path / "app" / "app.py").write_text("from special_pkg import Thing\nimport external", encoding="utf-8")
    (tmp_path / "library" / "pyproject.toml").write_text('[project]\nname = "special-pkg"', encoding="utf-8")
    map_repo(tmp_path / "app", owner)
    edges = link_repos([owner, library, third])
    assert len(edges) == 1
    assert (edges[0]["from"], edges[0]["to"]) == (owner.repo, library.repo)
    assert "special_pkg" in edges[0]["why"]


def test_other_links(tmp_path):
    from prometheus.repos.codemap import link_repos

    app, lib = [profile(tmp_path / n, n) for n in ("app", "lib")]
    (tmp_path / "app").mkdir()
    (tmp_path / "lib").mkdir()
    modules = tmp_path / "app" / ".gitmodules"
    modules.write_text('[submodule "vendor"]\n path = vendor\n url = ../lib.git\n', encoding="utf-8")
    edges = link_repos([app, lib])
    assert [(e["from"], e["to"]) for e in edges] == [(app.repo, lib.repo)]
    assert "submodule" in edges[0]["why"]
    modules.unlink()
    app.dependencies = lib.dependencies = [{"name": "requests", "source": "requirements.txt", "version": ""}]
    edges = link_repos([app, lib])
    assert len(edges) == 1 and "requests" in edges[0]["why"]


def test_untrusted_symbol_paths(tmp_path):
    from prometheus.actions.atomic_fs import find_symbol

    path = tmp_path / "codemap.json"
    path.write_text(json.dumps({"symbols": {"x": ["../outside.py:1", "C:/outside.py:1",
                                                 None, "source.py:2"]}}), encoding="utf-8")
    assert find_symbol(tmp_path, "x") == [{"path": "source.py", "line": 2}]


def test_fallbacks(tmp_path, monkeypatch):
    from prometheus.repos import codemap

    monkeypatch.setattr(codemap, "_grammar", lambda *args: None)
    path = tmp_path / "source.py"
    path.write_text("from pathlib import Path\nclass Box:\n def open(self): pass", encoding="utf-8")
    mapped = codemap.map_file(path)
    assert mapped["classes"] == [dict(symbol("Box", 2), methods=[symbol("open", 3)])]
    assert mapped["imports"] == ["pathlib"]
    path = tmp_path / "source.js"
    path.write_text("export default async function work() {}\nclass Box {}", encoding="utf-8")
    mapped = codemap.map_file(path)
    assert mapped["functions"] == [symbol("work", 1)]
    assert "pattern" in mapped["note"]


def test_link_fresh_profiles_without_a_saved_map(tmp_path):
    from prometheus.repos.codemap import link_repos

    app, lib = [profile(tmp_path / n, n) for n in ("app", "lib")]
    for p in (app, lib):
        (tmp_path / p.repo.split("/")[-1]).mkdir()
    (tmp_path / "app" / "app.py").write_text("import lib", encoding="utf-8")
    assert link_repos([app, lib]) == [{"from": app.repo, "to": lib.repo, "why": "imports lib"}]


def test_repo_cannot_supply_a_parser(tmp_path, monkeypatch):
    from prometheus.repos import codemap

    (tmp_path / "tree_sitter_python.py").write_text("raise AssertionError('repo module executed')",
                                                   encoding="utf-8")
    source = tmp_path / "example.py"
    source.write_text("def run(): pass", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "tree_sitter_python", raising=False)
    codemap._grammar.cache_clear()
    try:
        assert codemap.map_file(source)["functions"] == [symbol("run", 1)]
    finally:
        codemap._grammar.cache_clear()
