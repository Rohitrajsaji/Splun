"""Deterministic Python code scanning with the stdlib ``ast`` module.

Extracted per file:
  * classes (with base names), functions, methods (async included);
  * fields: assignments inside class bodies, e.g.
    ``email = Column(String, nullable=True)`` or ``email: Optional[str] = None``;
  * module-level assignments whose value is a call with keyword arguments,
    e.g. ``legacy_ref = Field(required=False)``.

For every field the literal keyword arguments are recorded, and an
``indicates`` verdict is derived from explicit signals only:

  nullable  - nullable/null/allow_none/allow_null=True, Optional[...]/``X | None``
              annotation, or a ``None`` default
  optional  - required=False, blank=True, optional=True
  mandatory - nullable/null=False, required=True, blank=False

Every symbol's exact source span (ast byte offsets) is stored verbatim in the
evidence vault as ``CODE_AST`` and indexed in ``code_symbols``.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Optional

from core.storage.db import ProjectMemoryDB

SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "node_modules",
     "site-packages", ".mypy_cache", ".pytest_cache", ".tox", "build", "dist"}
)
_NULLABLE_KEYS = ("nullable", "null", "allow_none", "allow_null")
_MISSING = object()


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _MISSING


def _keywords(call: ast.Call) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:  # **kwargs
            continue
        value = _literal(kw.value)
        out[kw.arg] = ast.unparse(kw.value) if value is _MISSING else value
    return out


def _annotation_is_optional(annotation: Optional[ast.AST]) -> bool:
    if annotation is None:
        return False
    text = ast.unparse(annotation)
    return bool(re.search(r"\bOptional\[", text) or re.search(r"\bNone\b", text))


def _field_details(value: Optional[ast.AST], annotation: Optional[ast.AST]) -> dict:
    details: dict[str, Any] = {
        "annotation": ast.unparse(annotation) if annotation is not None else None,
        "call": None,
        "keywords": {},
        "default": ast.unparse(value) if value is not None else None,
    }
    nullable: list[str] = []
    optional: list[str] = []
    mandatory: list[str] = []

    if isinstance(value, ast.Call):
        details["call"] = ast.unparse(value.func)
        kws = _keywords(value)
        details["keywords"] = kws
        for key in _NULLABLE_KEYS:
            if kws.get(key) is True:
                nullable.append(f"{key}=True")
            elif kws.get(key) is False:
                mandatory.append(f"{key}=False")
        if kws.get("required") is False:
            optional.append("required=False")
        elif kws.get("required") is True:
            mandatory.append("required=True")
        if kws.get("blank") is True:
            optional.append("blank=True")
        elif kws.get("blank") is False:
            mandatory.append("blank=False")
        if kws.get("optional") is True:
            optional.append("optional=True")
        if "default" in kws and kws["default"] is None:
            nullable.append("default=None")
    elif isinstance(value, ast.Constant) and value.value is None:
        nullable.append("default=None")

    if _annotation_is_optional(annotation):
        nullable.append(f"annotation={ast.unparse(annotation)}")

    details["indicators"] = nullable + optional + mandatory
    details["indicates"] = (
        "nullable" if nullable else "optional" if optional else "mandatory" if mandatory else None
    )
    return details


class _FileScanner:
    def __init__(self, rel_path: str, source: str):
        self.rel_path = rel_path
        self.source = source
        self.raw = source.encode("utf-8")
        self.line_offsets = [0]
        for line in source.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line.encode("utf-8")))
        self.symbols: list[dict] = []

    def _span(self, node: ast.AST) -> tuple[int, int]:
        start = self.line_offsets[node.lineno - 1] + node.col_offset
        end = self.line_offsets[node.end_lineno - 1] + node.end_col_offset
        return start, end

    def _add(self, node: ast.AST, name: str, kind: str, parent: Optional[str], details: dict) -> None:
        start, end = self._span(node)
        qualname = f"{parent}.{name}" if parent else name
        self.symbols.append(
            {
                "file_path": self.rel_path,
                "qualname": qualname,
                "symbol_name": name,
                "kind": kind,
                "parent": parent,
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "byte_start": start,
                "byte_end": end,
                "verbatim": self.raw[start:end].decode("utf-8"),
                "details": details,
            }
        )

    def scan(self, tree: ast.Module) -> list[dict]:
        self._visit_body(tree.body, parent=None, in_class=False)
        return self.symbols

    def _visit_body(self, body: list[ast.stmt], parent: Optional[str], in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._add(node, node.name, "class", parent,
                          {"bases": [ast.unparse(b) for b in node.bases],
                           "decorators": [ast.unparse(d) for d in node.decorator_list]})
                qual = f"{parent}.{node.name}" if parent else node.name
                self._visit_body(node.body, parent=qual, in_class=True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                arg_names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
                if args.vararg:
                    arg_names.append("*" + args.vararg.arg)
                if args.kwarg:
                    arg_names.append("**" + args.kwarg.arg)
                self._add(node, node.name, "method" if in_class else "function", parent,
                          {"args": arg_names,
                           "async": isinstance(node, ast.AsyncFunctionDef),
                           "decorators": [ast.unparse(d) for d in node.decorator_list]})
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if in_class or (isinstance(node.value, ast.Call) and node.value.keywords):
                    self._add(node, node.target.id, "field" if in_class else "assignment",
                              parent, _field_details(node.value, node.annotation))
            elif isinstance(node, ast.Assign):
                if not in_class and not (isinstance(node.value, ast.Call) and node.value.keywords):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._add(node, target.id, "field" if in_class else "assignment",
                                  parent, _field_details(node.value, None))


def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name


def scan_python_codebase(db: ProjectMemoryDB, project_id: str, root_dir: str) -> dict:
    """Scan ``root_dir`` and replace the project's code-symbol snapshot.

    Files that fail to parse are reported in ``errors`` and skipped.
    """
    db.require_project(project_id)
    root = Path(root_dir).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    all_symbols: list[dict] = []
    errors: list[dict] = []
    files_scanned = 0
    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        files_scanned += 1
        all_symbols.extend(_FileScanner(rel, source).scan(tree))

    with db.transaction():
        for sym in all_symbols:
            digest = hashlib.sha256(sym["verbatim"].encode("utf-8")).hexdigest()[:12]
            sym["evidence_id"] = f"CODE_AST:{sym['file_path']}:{sym['qualname']}:{digest}"
            sym["byte_range"] = f"{sym['byte_start']}-{sym['byte_end']}"
            db.add_evidence(
                project_id,
                sym["evidence_id"],
                "CODE_AST",
                f"{sym['file_path']}#L{sym['line_start']}-L{sym['line_end']}",
                sym["verbatim"],
                byte_range=sym["byte_range"],
            )
        db.replace_code_symbols(project_id, all_symbols)

    return {
        "files_scanned": files_scanned,
        "symbols_found": len(all_symbols),
        "errors": errors,
    }


def get_symbol_info(db: ProjectMemoryDB, project_id: str, symbol_name: str) -> list[dict]:
    """All symbols whose bare or qualified name equals ``symbol_name``, each
    with its verbatim ``CODE_AST`` evidence attached under ``evidence``."""
    matches = db.find_code_symbols(project_id, symbol_name)
    for m in matches:
        m["evidence"] = db.get_evidence(project_id, m["evidence_id"])
    return matches
