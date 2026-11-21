"""Index declarations in repository source without loading repository modules."""
import ast
from collections import defaultdict
from functools import lru_cache
from importlib import import_module
from importlib.util import find_spec
from itertools import islice
import json
import os
from pathlib import Path
import re
import site
import tempfile
from urllib.parse import urljoin, urlsplit

from prometheus.repos.profile import LANGUAGES, MAX_READ, RepoProfile, _cfg, _json, _read, _toml


GRAMMARS = {
    "Python": "python", "JavaScript": "javascript", "TypeScript": "typescript",
    "Rust": "rust", "Go": "go", "C#": "c_sharp", "Java": "java",
}
CLASSES = {"class_definition", "class_declaration", "abstract_class_declaration",
           "interface_declaration", "struct_declaration", "record_declaration", "enum_declaration",
           "struct_item", "enum_item", "trait_item"}
FUNCTIONS = {"function_definition", "function_declaration", "generator_function_declaration",
             "function_item", "function_signature_item", "method_declaration", "method_definition",
             "constructor_declaration", "method_signature"}
PATTERN = re.compile(r"^\s*(?:(?:export|default|pub|async|public|private|static|final|abstract)\s+)*"
                     r"(class|interface|struct|trait|enum|def|function|fn|func|fun|sub)\s+([^\W\d]\w*)")


@lru_cache(maxsize=8)
def _grammar(language, tsx):
    if language not in GRAMMARS:
        return None
    package = "tree_sitter_" + GRAMMARS[language]
    installed = [Path(p).resolve() for p in [*site.getsitepackages(), site.getusersitepackages()]]
    # A checkout on sys.path can shadow even a fixed, known package name.
    for name in ("tree_sitter", package):
        spec = find_spec(name)
        if not spec or not spec.origin or Path(spec.origin).resolve().parent.parent not in installed:
            return None
    try:
        from tree_sitter import Language
        module = import_module(package)
        if language == "TypeScript":
            return Language(module.language_tsx() if tsx else module.language_typescript())
        return Language(module.language())
    except (ImportError, OSError):
        return None


def _text(node):
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _imports(node):
    field = node.child_by_field_name
    if node.type == "import_from_statement":
        return [_text(field("module_name"))]
    if node.type == "import_statement" and not field("source"):
        return [_text(child.child_by_field_name("name") or child)
                for child in node.children_by_field_name("name")]
    if node.type in {"import_statement", "export_statement", "import_spec"}:
        return [_text(field("source") or field("path")).strip("'\"" + chr(96))]
    if node.type == "use_declaration":
        return [_text(field("argument"))]
    if node.type in {"import_declaration", "using_directive"} and node.named_children:
        child = node.named_children[-1]
        if child.type in {"identifier", "scoped_identifier", "qualified_name", "generic_name"}:
            return [_text(child)]
        if child.type == "asterisk":
            return [_text(node.named_children[0]) + ".*"]
    return []


def _tree_map(data, grammar, result):
    from tree_sitter import Parser

    root = Parser(grammar).parse(data).root_node
    stack = [(root, None)]
    external = defaultdict(list)
    while stack:
        node, methods = stack.pop()
        kind = node.type
        field = node.child_by_field_name
        name = _text(field("name"))
        item = {"name": name, "line": node.start_point.row + 1}
        is_go_class = kind == "type_spec" and field("type") and field("type").type in {"struct_type", "interface_type"}
        if name and (kind in CLASSES or is_go_class):
            methods = []
            result["classes"].append(dict(item, methods=methods))
        elif kind == "impl_item":
            receiver = _text(field("type")).partition("<")[0].split("::")[-1]
            methods = external[receiver]
        elif name and (kind in FUNCTIONS or
                       (kind == "variable_declarator" and field("value") and
                        field("value").type in {"arrow_function", "function_expression"})):
            receiver = field("receiver")
            if receiver and receiver.named_children:
                typename = _text(receiver.named_children[0].child_by_field_name("type"))
                methods = external[typename.lstrip("*").partition("[")[0]]
            (methods if methods is not None else result["functions"]).append(item)
            # Local functions are not class members.
            methods = None
        if kind.startswith(("import", "export", "use_", "using_")):
            result["imports"].extend(value for value in _imports(node) if value)
        stack.extend((child, methods) for child in reversed(node.named_children))
    for cls in result["classes"]:
        cls["methods"].extend(external.pop(cls["name"], []))
    for members in external.values():
        result["functions"].extend(members)


def _python_ast(source, result):
    root = ast.parse(source)
    stack = [(root, None)]
    while stack:
        node, methods = stack.pop()
        if isinstance(node, ast.ClassDef):
            methods = []
            result["classes"].append({"name": node.name, "line": node.lineno, "methods": methods})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            (methods if methods is not None else result["functions"]).append({"name": node.name, "line": node.lineno})
            methods = None
        elif isinstance(node, ast.Import):
            result["imports"].extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result["imports"].append("." * node.level + (node.module or ""))
        stack.extend((child, methods) for child in reversed(list(ast.iter_child_nodes(node))))


def map_file(path):
    path = Path(path)
    language = LANGUAGES.get(path.suffix.lower(), "Unknown")
    result = {"path": path.as_posix(), "language": language, "functions": [], "classes": [], "imports": []}
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("Expected a regular source file")
    with path.open("rb") as stream:
        data = stream.read(MAX_READ + 1)
    if len(data) > MAX_READ:
        raise ValueError("File exceeds the code map size limit")
    grammar = _grammar(language, path.suffix.lower() == ".tsx")
    if grammar is not None:
        _tree_map(data, grammar, result)
    elif language == "Python":
        try:
            _python_ast(data, result)
        except (SyntaxError, ValueError, RecursionError) as exc:
            result["note"] = f"Python syntax could not be mapped: {exc}"
    else:
        result["note"] = "Names only, matched by a simple pattern."
        for line, text in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            match = PATTERN.match(text)
            if match:
                kind, name = match.groups()
                item = {"name": name, "line": line}
                if kind in {"class", "interface", "struct", "trait", "enum"}:
                    result["classes"].append(dict(item, methods=[]))
                else:
                    result["functions"].append(item)
    result["imports"] = list(dict.fromkeys(result["imports"]))
    return result


def _candidates(folder):
    for directory, dirs, names in folder.walk():
        dirs[:] = sorted(d for d in dirs if d != ".git" and not (directory / d).is_junction())
        for name in sorted(names):
            path = directory / name
            if path.suffix.lower() not in LANGUAGES or path == folder / "profile.json" or path == folder / "codemap.json":
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_READ:
                    continue
                yield path
            except OSError:
                continue


def map_repo(folder, profile: RepoProfile, limit_files=2000):
    folder = Path(folder)
    if limit_files < 0:
        raise ValueError("limit_files must be nonnegative")
    files, symbols = [], defaultdict(list)
    sampled = False
    for path in _candidates(folder):
        if len(files) >= limit_files:
            sampled = True
            break
        try:
            item = map_file(path)
        except (OSError, ValueError):
            continue
        item["path"] = path.relative_to(folder).as_posix()
        files.append(item)
        for entry in item["functions"] + item["classes"]:
            for symbol in [entry, *entry.get("methods", [])]:
                symbols[symbol["name"]].append(f"{item['path']}:{symbol['line']}")
    result = {"files": files, "symbols": dict(symbols), "sampled": sampled}
    stream = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False)
    try:
        with stream:
            json.dump(result, stream, ensure_ascii=True)
        os.replace(stream.name, folder / "codemap.json")
    finally:
        Path(stream.name).unlink(missing_ok=True)
    return result


def _package_names(profile):
    folder = Path(profile.folder)
    names = {profile.repo.split("/")[-1]}
    for filename, section in (("pyproject.toml", "project"), ("Cargo.toml", "package")):
        metadata = _toml(folder / filename).get(section)
        if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
            names.add(metadata["name"])
    npm = _json(folder / "package.json").get("name")
    if isinstance(npm, str):
        names.add(npm)
    go = re.search(r"^module\s+(\S+)", _read(folder / "go.mod"), re.MULTILINE)
    if go:
        names.add(go[1])
    return {name.replace("-", "_") for name in names if name}


def _evidence(profile):
    folder = Path(profile.folder)
    path = folder / "codemap.json"
    imports = set()
    cached = None
    if path.is_file() and not path.is_symlink():
        try:
            with path.open(encoding="utf-8") as stream:
                mapping = json.load(stream)
            if isinstance(mapping, dict):
                cached = mapping.get("files")
        except (OSError, ValueError, RecursionError):
            pass
    if isinstance(cached, list):
        for item in cached:
            if isinstance(item, dict) and isinstance(item.get("imports"), list):
                imports.update(value for value in item["imports"] if isinstance(value, str))
    else:
        for candidate in islice(_candidates(folder), 2000):
            try:
                imports.update(map_file(candidate)["imports"])
            except (OSError, ValueError):
                continue
    modules = _cfg(folder / ".gitmodules")
    targets = set()
    for section in modules.sections():
        if section.startswith('submodule '):
            url = modules.get(section, "url", fallback="").replace("git@github.com:", "https://github.com/")
            parsed = urlsplit(urljoin("https://github.com/" + profile.repo + "/", url))
            if parsed.hostname == "github.com":
                targets.add(parsed.path.strip("/").removesuffix(".git").casefold())
    deps = set()
    for dep in profile.dependencies:
        source = dep["source"]
        ecosystem = ("npm" if source.startswith("package.json") else
                     source if source in {"go.mod", "Cargo.toml"} else "python")
        deps.add((ecosystem, dep["name"]))
    return sorted(imports), targets, deps


def link_repos(profiles):
    own = {p.repo: p for p in profiles if p.own}
    packages = {repo: _package_names(p) for repo, p in own.items()}
    evidence = {repo: _evidence(p) for repo, p in own.items()}
    edges = []
    names = sorted(own)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            connected = False
            for source, target in ((left, right), (right, left)):
                imports, submodules, _ = evidence[source]
                why = ""
                if target.casefold() in submodules:
                    why = "git submodule"
                else:
                    for value in imports:
                        normalized = value.replace("-", "_")
                        if any(normalized == name or normalized.startswith((name + ".", name + "/", name + "::"))
                               for name in packages[target]):
                            why = f"imports {value}"
                            break
                if why:
                    edges.append({"from": source, "to": target, "why": why})
                    connected = True
            shared = evidence[left][2] & evidence[right][2]
            if shared and not connected:
                edges.append({"from": left, "to": right, "why": "shared dependency " + sorted(shared)[0][1]})
    return edges
