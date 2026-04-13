#!/usr/bin/env python3
"""Create an LLM-friendly text digest from a local directory.

This is a small, local-only alternative to gitingest for cases where a private
repository cannot be ingested remotely. It walks a directory, prints a tree of
included files, and then appends the content of each included text file into a
single TXT file.

Default behavior:
- local directory only
- include files up to 50 KB
- skip obvious binary / cache / build directories
- keep the output format easy for LLMs to read

Examples
--------
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours -o freedance_digest.txt
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours --max-file-kb 200
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours -i "*.py" -i "*.md"
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours -e "data/*" -e "*.pt"
python local_dir_ingest.py /root/dev/Threedv/Freedance_ours -o -
"""

from __future__ import annotations

import argparse
import codecs
import fnmatch
import json
import locale
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_MAX_FILE_KB = 50
DEFAULT_OUTPUT_FILE = "digest.txt"
SEPARATOR = "=" * 48
_SAMPLE_SIZE = 4096

# Directories that are almost always noise for LLM ingestion.
DEFAULT_IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
}

# Obvious binary / compiled / media / archive patterns.
DEFAULT_IGNORED_FILE_PATTERNS = {
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.o",
    "*.obj",
    "*.a",
    "*.class",
    "*.jar",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.bz2",
    "*.xz",
    "*.7z",
    "*.pdf",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.mp3",
    "*.wav",
    "*.mp4",
    "*.mov",
    "*.avi",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.bin",
    ".DS_Store",
}


@dataclass(slots=True)
class CollectedFile:
    """A text file that will be included in the digest."""

    relative_path: str
    size_bytes: int
    content: str


@dataclass(slots=True)
class Stats:
    """Counters collected during directory traversal."""

    included_files: int = 0
    included_bytes: int = 0
    pruned_directories: int = 0
    skipped_pattern: int = 0
    skipped_too_large: int = 0
    skipped_binary: int = 0
    skipped_unreadable: int = 0


@dataclass(slots=True)
class TreeNode:
    """Simple tree structure for rendering included files."""

    name: str
    is_dir: bool = True
    children: dict[str, "TreeNode"] = field(default_factory=dict)

    def add_file(self, parts: Sequence[str]) -> None:
        """Insert one relative file path into the tree."""
        if not parts:
            return

        node = self
        for part in parts[:-1]:
            child = node.children.get(part)
            if child is None:
                child = TreeNode(name=part, is_dir=True)
                node.children[part] = child
            node = child

        leaf_name = parts[-1]
        if leaf_name not in node.children:
            node.children[leaf_name] = TreeNode(name=leaf_name, is_dir=False)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Create one LLM-friendly TXT digest from a local directory.",
    )
    parser.add_argument(
        "source",
        help="Local directory to ingest.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help="Output TXT path. Use '-' to write to stdout. Default: digest.txt",
    )
    parser.add_argument(
        "--max-file-kb",
        type=int,
        default=DEFAULT_MAX_FILE_KB,
        help=f"Include only files up to this size in KB. Default: {DEFAULT_MAX_FILE_KB}",
    )
    parser.add_argument(
        "-i",
        "--include",
        action="append",
        default=[],
        help="Glob pattern(s) to include. Repeatable. Example: -i '*.py' -i '*.md'",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        default=[],
        help="Glob pattern(s) to exclude. Repeatable. Example: -e 'data/*' -e '*.pt'",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinked directories while walking the tree.",
    )
    parser.add_argument(
        "--include-notebook-output",
        action="store_true",
        help="When reading .ipynb, include cell outputs as comments.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"Error: source does not exist: {source}", file=sys.stderr)
        return 1
    if not source.is_dir():
        print(f"Error: source must be a directory: {source}", file=sys.stderr)
        return 1
    if args.max_file_kb <= 0:
        print("Error: --max-file-kb must be a positive integer.", file=sys.stderr)
        return 1

    include_patterns = normalize_patterns(args.include)
    exclude_patterns = normalize_patterns(args.exclude)
    output_path = None if args.output == "-" else Path(args.output).expanduser().resolve()

    files, stats = collect_files(
        source=source,
        max_file_bytes=args.max_file_kb * 1024,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=args.follow_symlinks,
        include_notebook_output=args.include_notebook_output,
        output_path=output_path,
    )

    digest_text = build_digest_text(
        source=source,
        files=files,
        stats=stats,
        max_file_bytes=args.max_file_kb * 1024,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )

    if args.output == "-":
        sys.stdout.write(digest_text)
        sys.stdout.flush()
    else:
        assert output_path is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(digest_text, encoding="utf-8")
        print(f"Wrote digest to: {output_path}")
        print(f"Included files: {stats.included_files}")
        print(f"Included bytes: {human_size(stats.included_bytes)}")

    return 0


def normalize_patterns(values: Iterable[str]) -> list[str]:
    """Normalize repeated / comma-separated glob patterns."""
    patterns: list[str] = []
    for value in values:
        for part in value.split(","):
            normalized = part.strip().replace("\\", "/")
            if normalized:
                patterns.append(normalized)
    return patterns


def collect_files(
    source: Path,
    max_file_bytes: int,
    include_patterns: Sequence[str],
    exclude_patterns: Sequence[str],
    follow_symlinks: bool,
    include_notebook_output: bool,
    output_path: Path | None,
) -> tuple[list[CollectedFile], Stats]:
    """Walk the directory and collect readable text files."""
    stats = Stats()
    collected: list[CollectedFile] = []

    for root, dirnames, filenames in os.walk(source, topdown=True, followlinks=follow_symlinks):
        root_path = Path(root)
        rel_root = "" if root_path == source else root_path.relative_to(source).as_posix()

        pruned_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            dir_path = root_path / dirname

            if not follow_symlinks and dir_path.is_symlink():
                stats.pruned_directories += 1
                continue

            rel_dir = f"{rel_root}/{dirname}" if rel_root else dirname
            if should_prune_directory(rel_dir, dirname, exclude_patterns):
                stats.pruned_directories += 1
                continue

            pruned_dirnames.append(dirname)

        dirnames[:] = pruned_dirnames

        for filename in sorted(filenames):
            file_path = root_path / filename
            rel_path = f"{rel_root}/{filename}" if rel_root else filename
            rel_path = rel_path.replace(os.sep, "/")

            if output_path is not None and same_path(file_path, output_path):
                continue

            if file_path.is_symlink() and not follow_symlinks:
                stats.skipped_pattern += 1
                continue

            if should_skip_file(rel_path, filename, exclude_patterns, include_patterns):
                stats.skipped_pattern += 1
                continue

            try:
                size_bytes = file_path.stat().st_size
            except OSError:
                stats.skipped_unreadable += 1
                continue

            if size_bytes > max_file_bytes:
                stats.skipped_too_large += 1
                continue

            if file_path.suffix.lower() == ".ipynb":
                status, content = read_notebook(file_path, include_output=include_notebook_output)
            else:
                status, content = read_text_file(file_path)

            if status == "binary":
                stats.skipped_binary += 1
                continue
            if status == "unreadable":
                stats.skipped_unreadable += 1
                continue

            collected.append(
                CollectedFile(
                    relative_path=rel_path,
                    size_bytes=size_bytes,
                    content=content,
                )
            )
            stats.included_files += 1
            stats.included_bytes += size_bytes

    collected.sort(key=lambda item: item.relative_path.lower())
    return collected, stats


def should_prune_directory(rel_dir: str, dirname: str, exclude_patterns: Sequence[str]) -> bool:
    """Return True when a directory should be skipped entirely."""
    if dirname in DEFAULT_IGNORED_DIRS:
        return True
    return matches_any(rel_dir, exclude_patterns, is_dir=True)


def should_skip_file(
    rel_path: str,
    filename: str,
    exclude_patterns: Sequence[str],
    include_patterns: Sequence[str],
) -> bool:
    """Return True when a file should not be included."""
    if matches_any(filename, DEFAULT_IGNORED_FILE_PATTERNS) or matches_any(rel_path, DEFAULT_IGNORED_FILE_PATTERNS):
        return True
    if matches_any(rel_path, exclude_patterns):
        return True
    if include_patterns and not matches_any(rel_path, include_patterns):
        return True
    return False


def matches_any(path_str: str, patterns: Sequence[str], is_dir: bool = False) -> bool:
    """Glob-match a relative path against a list of patterns."""
    if not patterns:
        return False

    normalized = path_str.replace("\\", "/").strip("/")
    basename = Path(normalized).name
    candidates = {normalized, basename}
    if is_dir:
        candidates.add(normalized + "/")
        candidates.add(basename + "/")

    for pattern in patterns:
        normalized_pattern = pattern.replace("\\", "/").strip()
        if not normalized_pattern:
            continue
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, normalized_pattern):
                return True
    return False


def read_text_file(path: Path) -> tuple[str, str]:
    """Read one text file with simple binary detection and encoding fallbacks."""
    try:
        sample = path.read_bytes()[:_SAMPLE_SIZE]
    except OSError:
        return "unreadable", ""

    if looks_binary(sample):
        return "binary", ""

    for encoding in preferred_encodings():
        try:
            return "ok", path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return "unreadable", ""

    return "unreadable", ""


def preferred_encodings() -> list[str]:
    """Return a small, deduplicated encoding fallback list."""
    candidates = [
        locale.getpreferredencoding(False) or "utf-8",
        "utf-8",
        "utf-8-sig",
        "cp949",
        "euc-kr",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = item.lower()
        if key not in seen:
            ordered.append(item)
            seen.add(key)
    return ordered


def looks_binary(sample: bytes) -> bool:
    """Cheap binary detection that still allows UTF-8 Korean text."""
    if not sample:
        return False

    if sample.startswith(
        (
            codecs.BOM_UTF8,
            codecs.BOM_UTF16_BE,
            codecs.BOM_UTF16_LE,
            codecs.BOM_UTF32_BE,
            codecs.BOM_UTF32_LE,
        )
    ):
        return False

    if b"\x00" in sample:
        return True

    suspicious = 0
    for byte in sample:
        if byte < 7 or (14 <= byte < 32 and byte not in (9, 10, 12, 13)):
            suspicious += 1

    return suspicious / max(len(sample), 1) > 0.30


def read_notebook(path: Path, include_output: bool) -> tuple[str, str]:
    """Convert a Jupyter notebook into a readable script-like text block."""
    try:
        raw_text = path.read_text(encoding="utf-8")
        notebook = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return read_text_file(path)

    cells = notebook.get("cells")
    if not isinstance(cells, list):
        return "ok", raw_text

    parts = ["# Jupyter notebook converted to Python-like text."]

    for cell in cells:
        if not isinstance(cell, dict):
            continue

        cell_type = cell.get("cell_type")
        source = join_cell_source(cell.get("source", []))
        if not source.strip():
            continue

        if cell_type in {"markdown", "raw"}:
            parts.append(f'"""\n{source.rstrip()}\n"""')
            continue

        if cell_type == "code":
            block = source.rstrip()
            if include_output:
                output_lines = extract_notebook_outputs(cell.get("outputs", []))
                if output_lines:
                    block += "\n# Output:\n#   " + "\n#   ".join(output_lines)
            parts.append(block)
            continue

        parts.append(source.rstrip())

    return "ok", "\n\n".join(parts).rstrip() + "\n"


def join_cell_source(source: object) -> str:
    """Normalize notebook cell source into a string."""
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        return "".join(str(item) for item in source)
    return str(source)


def extract_notebook_outputs(outputs: object) -> list[str]:
    """Extract simple text outputs from notebook cells."""
    if not isinstance(outputs, list):
        return []

    lines: list[str] = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        output_type = output.get("output_type")
        if output_type == "stream":
            lines.extend(split_output_text(output.get("text", [])))
        elif output_type in {"execute_result", "display_data"}:
            data = output.get("data", {})
            if isinstance(data, dict):
                lines.extend(split_output_text(data.get("text/plain", [])))
        elif output_type == "error":
            ename = output.get("ename", "Error")
            evalue = output.get("evalue", "")
            lines.append(f"{ename}: {evalue}".strip())
    return [line for line in (item.strip("\n") for item in lines) if line]


def split_output_text(value: object) -> list[str]:
    """Turn notebook output payloads into a list of lines."""
    if isinstance(value, str):
        return value.splitlines() or [value]
    if isinstance(value, list):
        joined = "".join(str(item) for item in value)
        return joined.splitlines() or ([joined] if joined else [])
    text = str(value)
    return text.splitlines() or [text]


def build_digest_text(
    source: Path,
    files: Sequence[CollectedFile],
    stats: Stats,
    max_file_bytes: int,
    include_patterns: Sequence[str],
    exclude_patterns: Sequence[str],
) -> str:
    """Create the final digest text."""
    tree = build_tree_text(source, files)

    lines = [
        f"Directory: {source}",
        f"Files analyzed: {stats.included_files}",
        f"Included bytes: {stats.included_bytes:,} ({human_size(stats.included_bytes)})",
        f"Max included file size: {human_size(max_file_bytes)}",
        f"Pruned directories: {stats.pruned_directories}",
        f"Skipped by pattern/default ignore: {stats.skipped_pattern}",
        f"Skipped as too large: {stats.skipped_too_large}",
        f"Skipped as binary: {stats.skipped_binary}",
        f"Skipped as unreadable: {stats.skipped_unreadable}",
    ]

    if include_patterns:
        lines.append("Include patterns: " + ", ".join(include_patterns))
    if exclude_patterns:
        lines.append("Exclude patterns: " + ", ".join(exclude_patterns))

    lines.append("")
    lines.append("Directory structure:")
    lines.append(tree.rstrip())
    lines.append("")

    for item in files:
        lines.append(SEPARATOR)
        lines.append(f"FILE: {item.relative_path}")
        lines.append(SEPARATOR)
        lines.append(item.content.rstrip("\n"))
        lines.append("")
        lines.append("")

    if not files:
        lines.append(SEPARATOR)
        lines.append("No text files matched the current options.")
        lines.append(SEPARATOR)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_tree_text(source: Path, files: Sequence[CollectedFile]) -> str:
    """Render a tree using only the files that were actually included."""
    root = TreeNode(name=source.name, is_dir=True)
    for item in files:
        parts = [part for part in item.relative_path.split("/") if part]
        root.add_file(parts)
    return render_tree(root)


def render_tree(node: TreeNode, prefix: str = "", is_last: bool = True) -> str:
    """Recursively render a unicode tree."""
    branch = "└── " if is_last else "├── "
    label = node.name + ("/" if node.is_dir else "")
    result = f"{prefix}{branch}{label}\n"

    if not node.children:
        return result

    next_prefix = prefix + ("    " if is_last else "│   ")
    children = sorted(node.children.values(), key=tree_sort_key)

    for index, child in enumerate(children):
        result += render_tree(child, prefix=next_prefix, is_last=index == len(children) - 1)

    return result


def tree_sort_key(node: TreeNode) -> tuple[int, str]:
    """Sort similar to gitingest: README first, then files, then directories."""
    name = node.name.lower()
    if not node.is_dir:
        if name == "readme" or name.startswith("readme."):
            return (0, name)
        return (1 if not name.startswith(".") else 2, name)
    return (3 if not name.startswith(".") else 4, name)


def same_path(left: Path, right: Path) -> bool:
    """Best-effort path equality without crashing on missing files."""
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def human_size(num_bytes: int) -> str:
    """Render a byte count in a compact human-readable format."""
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"


if __name__ == "__main__":
    raise SystemExit(main())
