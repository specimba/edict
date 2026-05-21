#!/usr/bin/env python3
"""
Filter script for docs/docstring_gaps_phase_a.json.

Applies three filters to the list of functions missing docstrings:
  1. Exclude test files (file starts with "tests/")
  2. Exclude private members (func starts with "_" but is NOT a dunder method)
  3. Exclude local/inner functions detected by:
     a) AST depth detection – functions whose name appears at depth > 1 in the AST
        (nested inside another FunctionDef/AsyncFunctionDef)
     b) Name-file deduplication – if the same name appears >1 time in the same file,
        keep only the first occurrence (by line number); marked "local" via scope scan

Usage:
    python scripts/filter_docstring_gaps.py
"""

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent
INPUT = REPO / "docs" / "docstring_gaps_phase_a.json"


def is_test_file(entry):
    return entry["file"].startswith("tests/")


def is_private_func(func_name):
    """True if a non-dunder private member (starts with '_' but NOT '__name__' form)."""
    return func_name.startswith("_") and not (
        func_name.startswith("__") and func_name.endswith("__")
    )


def parse_functions(filepath):
    """Parse one file and return:
       - funcs_by_line : { line : (name, depth) }
       - first_by_name  : { name : first_line_occurrence }
    Depth = number of enclosing FunctionDef/AsyncFunctionDef scopes (>0 means nested).
    """
    src = (REPO / filepath).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, str(filepath))

    funcs_by_line = {}  # line -> (name, nesting_depth)
    first_by_name = {}  # name -> first_line

    class _V(ast.NodeVisitor):
        def __init__(self):
            self._depth = 0

        def _visit_func(self, node):
            name = node.name
            depth = self._depth
            # Record every (name, line) the AST says defines a function.
            # depth > 0 → IS nested/local
            funcs_by_line[node.lineno] = (name, depth > 0)
            # Track first occurrence of each name in this file at module depth
            if depth == 0:
                if name not in first_by_name:
                    first_by_name[name] = node.lineno
            # Recurse with increased depth
            self._depth += 1
            self.generic_visit(node)
            self._depth -= 1

        def visit_FunctionDef(self, node):
            self._visit_func(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_func(node)

    _V().visit(tree)
    return funcs_by_line, first_by_name


def main():
    print("Loading JSON…")
    raw = json.loads(INPUT.read_text(encoding="utf-8"))
    total = len(raw)
    print(f"Original: {total} entries")

    # ── Filter 1: test files ──────────────────────────────────────────────
    non_test = [e for e in raw if not is_test_file(e)]
    removed_tests = total - len(non_test)
    print(f"After removing tests ({removed_tests}): {len(non_test)}")

    # ── Filter 2: private members ─────────────────────────────────────────
    public_non_test = [e for e in non_test if not is_private_func(e["func"])]
    removed_private = len(non_test) - len(public_non_test)
    print(f"After removing private ({removed_private}): {len(public_non_test)}")

    # ── Filter 3: local/inner functions (AST depth + name-file dedup) ─────
    removed_local = 0
    final = []

    # Parse each file exactly once
    file_cache = {}  # filepath -> (funcs_by_line, first_by_name)

    # Group remaining entries by file
    entries_by_file = defaultdict(list)
    for e in public_non_test:
        entries_by_file[e["file"]].append(e)

    for filepath in sorted(entries_by_file.keys()):
        entries = entries_by_file[filepath]

        # Parse (or reuse cached result)
        if filepath not in file_cache:
            file_cache[filepath] = parse_functions(filepath)
        funcs_by_line, first_by_name = file_cache[filepath]

        # Sort entries by line so that dedup "keep first" is deterministic
        sorted_entries = sorted(entries, key=lambda e: e["line"])
        names_seen = set()

        for e in sorted_entries:
            line = e["line"]
            name = e["func"]
            remove = False

            # Rule a) AST depth – name at the exact line is a nested function
            if line in funcs_by_line:
                _, is_nested = funcs_by_line[line]
                if is_nested:
                    remove = True

            if not remove:
                # Rule b) Dedup – same name appears again in the same file
                # We only keep the FIRST occurrence (sorted by line ascending)
                if name in names_seen:
                    remove = True
                else:
                    names_seen.add(name)

            if remove:
                removed_local += 1
            else:
                final.append(e)

    print(f"After removing local/inner ({removed_local}): {len(final)}")
    print(f"Final: {len(final)} entries")

    # ── Write output ─────────────────────────────────────────────────────
    INPUT.write_text(json.dumps(final, indent=2), encoding="utf-8")
    print(f"\nWrote filtered output to {INPUT}")
    sys.exit(0)


if __name__ == "__main__":
    main()
