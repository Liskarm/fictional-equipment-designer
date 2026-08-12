#!/usr/bin/env python3
"""Maintain a fail-closed fictional-equipment canon registry.

Exit codes: 0 success, 1 validation findings, 2 command-line usage,
3 content/registry/transaction precondition error, 4 registry not found.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
import uuid


MARKER = ".equipment-registry.json"
JOURNAL = ".equipment-registry-transaction.json"
LOCK_FILE = ".equipment-registry.lock"
ENTRY_DIR = "equipment"
DECISIONS = "canon-decisions.jsonl"
RESOLUTIONS = "conflict-resolutions.jsonl"
MARKER_SCHEMA = 3
JOURNAL_SCHEMA = 1
STATUSES = {"Established", "Proposed", "Speculative", "Deprecated", "Contradicted"}
PROTECTED_STATUSES = {"Established", "Deprecated", "Contradicted"}
RELATION_TYPES = {
    "variant_of", "successor_to", "predecessor_to", "supersedes",
    "manufactured_by", "used_by", "issued_by", "maintained_by",
    "powered_by", "compatible_with", "requires", "counters",
    "countered_by", "appears_in", "associated_with", "contradicts",
}
RELATION_SCOPES = {"internal", "external", "unresolved"}
META_PREFIX = "<!-- equipment-registry: "
META_LINE_RE = re.compile(r"\A<!-- equipment-registry: ([^\r\n]*) -->\r?\n?")
WIKI_RE = re.compile(r"\[\[([^\]\r\n|#]+)(?:#[^\]\r\n|]+)?(?:\|[^\]\r\n]+)?\]\]")
VISIBLE_STATUS_RE = re.compile(r"^\*\*Canon status:\*\*[ \t]*(.*?)[ \t]*$", re.M | re.I)
TXN_RE = re.compile(r"\.equipment-registry-txn-([0-9a-f]{32})\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MANAGED_STATIC = {
    MARKER, "CHANGELOG.md", "canon-status.md", "conflicts.md",
    DECISIONS, RESOLUTIONS,
}


class RegistryError(RuntimeError):
    """A handled registry, content, or transaction error."""


class RegistryNotFound(RegistryError):
    """The requested registry root does not exist."""


class LockConflict(RegistryError):
    """Another process holds the registry writer lock."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value: str) -> str:
    value = clean_field(value, "timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise RegistryError("timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def source_timestamp(path: Path, override: str | None) -> str:
    if override:
        return parse_timestamp(override)
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    return stamp.replace(microsecond=0).isoformat()


def derive_timestamp(text: str, path: Path, override: str | None) -> str:
    if override:
        return parse_timestamp(override)
    supplied = parse_bold(text, "Last reviewed")
    if supplied:
        return parse_timestamp(supplied)
    return source_timestamp(path, None)


def norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def clean_field(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
    maximum: int = 2000,
) -> str:
    if not isinstance(value, str):
        raise RegistryError(f"{field} must be a string")
    result = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not result:
        raise RegistryError(f"{field} cannot be empty")
    if len(result) > maximum:
        raise RegistryError(f"{field} exceeds {maximum} characters")
    if "-->" in result:
        raise RegistryError(f"{field} cannot contain an HTML comment terminator")
    for character in result:
        code = ord(character)
        if character in "\r\n" or code == 0 or (code < 32 and character != "\t") or code == 127:
            raise RegistryError(f"{field} contains a forbidden control character")
    return result


def dedupe_strings(values: list[str], field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = clean_field(raw, field)
        key = norm(value)
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def dedupe_relationships(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for relation in values:
        checked = validate_relationship(relation)
        key = (checked["type"], checked["scope"], norm(checked["target"]))
        if key not in seen:
            result.append(checked)
            seen.add(key)
    return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker and checker())
    except OSError as exc:
        raise RegistryError(f"Cannot inspect path safety for {path}: {exc}") from exc


def canonical_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RegistryError("transaction paths must be nonempty canonical POSIX paths")
    if re.match(r"^[A-Za-z]:", value) or value.startswith(("/", "//")):
        raise RegistryError(f"absolute transaction path is forbidden: {value!r}")
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise RegistryError(f"non-contained transaction path is forbidden: {value!r}")
    return pure.as_posix()


def managed_relative(value: str) -> str:
    relative = canonical_relative(value)
    if relative in MANAGED_STATIC:
        return relative
    if relative == f"{ENTRY_DIR}/_index.md":
        return relative
    if re.fullmatch(rf"{ENTRY_DIR}/[^/]+\.md", relative) and not Path(relative).name.startswith("_"):
        return relative
    raise RegistryError(f"transaction target is not registry-managed: {relative}")


def safe_path(root: Path, relative: str, *, managed: bool = True) -> Path:
    relative = managed_relative(relative) if managed else canonical_relative(relative)
    root_real = root.resolve()
    candidate = root_real.joinpath(*PurePosixPath(relative).parts)
    try:
        candidate.resolve(strict=False).relative_to(root_real)
    except (OSError, ValueError) as exc:
        raise RegistryError(f"path escapes registry root: {relative}") from exc
    current = root_real
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() and is_reparse(current):
            raise RegistryError(f"symlink or junction is forbidden in registry path: {relative}")
    return candidate


def read_utf8(path: Path, *, allow_bom: bool = False) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        if not allow_bom:
            raise RegistryError(f"UTF-8 BOM is not allowed in registry file: {path}")
        data = data[3:]
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RegistryError(f"File is not strict UTF-8: {path}") from exc
    if "\ufffd" in text:
        raise RegistryError(f"File contains a Unicode replacement character: {path}")
    return text


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write(path: Path, data: str) -> None:
    atomic_write_bytes(path, data.encode("utf-8"))


def read_json(path: Path) -> dict:
    try:
        value = json.loads(read_utf8(path))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"JSON root must be an object: {path}")
    return value


def validate_uuid(value: object, field: str) -> str:
    value = clean_field(value, field, maximum=64)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise RegistryError(f"{field} must be a UUID") from exc
    return str(parsed)


def history_anchor(records: list[dict]) -> dict[str, object]:
    return {
        "count": len(records),
        "head_sha256": records[-1].get("record_sha256", "") if records else "",
    }


def marker_with_history_anchors(
    marker: dict,
    decisions: list[dict],
    resolutions: list[dict],
) -> dict:
    return {
        **marker,
        "history_anchors": {
            DECISIONS: history_anchor(decisions),
            RESOLUTIONS: history_anchor(resolutions),
        },
    }


def marker_text(marker: dict) -> str:
    return json.dumps(marker, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def load_marker(root: Path) -> dict:
    marker_path = root / MARKER
    if not marker_path.is_file() or is_reparse(marker_path):
        raise RegistryNotFound(f"Not an equipment registry: {root}")
    marker = read_json(marker_path)
    expected = {"schema", "kind", "registry_id", "created", "history_anchors"}
    if set(marker) != expected:
        raise RegistryError(f"Registry marker has an invalid schema: {marker_path}")
    if marker["schema"] != MARKER_SCHEMA or marker["kind"] != "equipment-canon-registry":
        raise RegistryError(f"Unsupported registry marker: {marker_path}")
    marker["registry_id"] = validate_uuid(marker["registry_id"], "registry_id")
    marker["created"] = parse_timestamp(marker["created"])
    anchors = marker["history_anchors"]
    if not isinstance(anchors, dict) or set(anchors) != {DECISIONS, RESOLUTIONS}:
        raise RegistryError(f"Registry marker has invalid history anchors: {marker_path}")
    for relative in (DECISIONS, RESOLUTIONS):
        anchor = anchors[relative]
        if not isinstance(anchor, dict) or set(anchor) != {"count", "head_sha256"}:
            raise RegistryError(f"Registry marker has invalid {relative} anchor")
        count = anchor["count"]
        head = anchor["head_sha256"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RegistryError(f"Registry marker has invalid {relative} count")
        if not isinstance(head, str) or (
            (count == 0 and head != "")
            or (count > 0 and not SHA_RE.fullmatch(head))
        ):
            raise RegistryError(f"Registry marker has invalid {relative} head digest")
    return marker


def find_registry(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / MARKER).exists():
            load_marker(candidate)
            return candidate
    return None


def require_registry(value: str | None, fallback: Path | None = None) -> Path:
    if value:
        root = Path(value).resolve()
        load_marker(root)
        return root
    root = find_registry(fallback or Path.cwd())
    if root is None:
        raise RegistryNotFound("No equipment registry found in this directory or its parents")
    return root


def pending_transaction(root: Path) -> bool:
    journal = root / JOURNAL
    if not journal.exists():
        return False
    if not journal.is_file() or is_reparse(journal):
        raise RegistryError("Transaction journal is not a safe regular file")
    return True


class RegistryLock(AbstractContextManager["RegistryLock"]):
    def __init__(self, root: Path):
        self.path = root / LOCK_FILE
        self.handle = None

    def __enter__(self) -> "RegistryLock":
        if not self.path.is_file() or is_reparse(self.path):
            raise RegistryError(f"Registry lock file is missing or unsafe: {self.path}")
        self.handle = self.path.open("r+b", buffering=0)
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise LockConflict("Another registry writer holds the exclusive lock") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def create_lock_file(root: Path) -> bool:
    path = root / LOCK_FILE
    if path.exists():
        if not path.is_file() or is_reparse(path) or path.stat().st_size < 1:
            raise RegistryError(f"Registry lock file is unsafe: {path}")
        return False
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, b"0")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(root)
    return True


def parse_bold(text: str, label: str) -> str | None:
    match = re.search(
        rf"^\*\*{re.escape(label)}:\*\*[ \t]*(.*?)[ \t]*$",
        text,
        re.M | re.I,
    )
    return match.group(1).strip() if match else None


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"^##[ \t]+{re.escape(heading)}[ \t]*\r?$\r?\n"
        rf"(.*?)(?=^##[ \t]+|\Z)",
        text,
        re.M | re.S | re.I,
    )
    return match.group(1).strip() if match else ""


def parse_aliases(text: str) -> list[str]:
    raw = parse_bold(text, "Aliases / designation")
    if raw is None:
        raw = parse_bold(text, "Aliases")
    if not raw:
        return []
    return dedupe_strings(
        [part.strip() for part in re.split(r"[,;|]", raw) if part.strip()],
        "alias",
    )


def parse_sources(text: str) -> list[str]:
    body = section(text, "Sources")
    result: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith(("-", "*")):
            value = stripped[1:].strip()
        elif stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not cells or all(cell and set(cell) <= {"-", ":"} for cell in cells):
                continue
            if norm(cells[0]) in {"source", "sources"}:
                continue
            value = cells[0]
        else:
            continue
        if value and not re.fullmatch(r"\[[^\]]*\]", value):
            checked = clean_field(value, "source", maximum=1000)
            if is_placeholder_source(checked):
                raise RegistryError(f"Source is only a placeholder: {checked}")
            result.append(checked)
    return dedupe_strings(result, "source")


def is_placeholder_source(value: str) -> bool:
    """Match only complete placeholder values, never substrings of real citations."""
    key = norm(value).rstrip(".。").strip()
    return key in {
        "tbd", "todo", "unknown", "none", "n/a", "n-a", "n.a", "n.a.", "na",
        "not available", "not applicable", "unspecified",
        "待定", "未定", "未知", "不明", "暂无", "无",
    }


def parse_relationship(value: str) -> dict[str, str]:
    first, separator, remainder = value.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError("relationship must use TYPE:TARGET or TYPE:SCOPE:TARGET")
    kind = first.strip()
    possible_scope, second_separator, scoped_target = remainder.partition(":")
    if second_separator and possible_scope.strip().lower() in RELATION_SCOPES:
        scope, target = possible_scope.strip(), scoped_target.strip()
    else:
        scope = "internal"
        target = remainder.strip()
    kind, scope = kind.lower(), scope.lower()
    if kind not in RELATION_TYPES:
        raise argparse.ArgumentTypeError(
            f"unknown relationship type {kind!r}; choose from {', '.join(sorted(RELATION_TYPES))}"
        )
    if scope not in RELATION_SCOPES:
        raise argparse.ArgumentTypeError(
            f"unknown relationship scope {scope!r}; choose from {', '.join(sorted(RELATION_SCOPES))}"
        )
    try:
        target = clean_field(target, "relationship target")
    except RegistryError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return {"type": kind, "scope": scope, "target": target}


def validate_relationship(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"type", "scope", "target"}:
        raise RegistryError("relationship must contain exactly type, scope, and target")
    kind = clean_field(value["type"], "relationship type").lower()
    scope = clean_field(value["scope"], "relationship scope").lower()
    target = clean_field(value["target"], "relationship target")
    if kind not in RELATION_TYPES:
        raise RegistryError(f"unknown relationship type: {kind}")
    if scope not in RELATION_SCOPES:
        raise RegistryError(f"unknown relationship scope: {scope}")
    return {"type": kind, "scope": scope, "target": target}


def source_relationships(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    body = section(text, "Relationships")
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"-", "*"}:
            continue
        match = re.fullmatch(
            r"\s*[-*]\s*([a-z_]+)\s*:\s*(?:(internal|external|unresolved)\s*:)?\s*"
            r"(?:\[\[([^\]\r\n|#]+)\]\]|([^\r\n]+))\s*",
            line,
            re.I,
        )
        if not match:
            if stripped.startswith(("-", "*")):
                raise RegistryError(f"Malformed relationship row: {stripped}")
            continue
        kind = match.group(1).lower()
        scope = (match.group(2) or "internal").lower()
        target = (match.group(3) or match.group(4) or "").strip()
        if kind not in RELATION_TYPES:
            raise RegistryError(f"Unknown relationship type in source: {kind}")
        result.append(validate_relationship({"type": kind, "scope": scope, "target": target}))
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        kind = cells[0].lower()
        if kind in {"type", "---", ""} or set(kind) <= {"-", ":"}:
            continue
        target = re.sub(r"^\[\[|\]\]$", "", cells[1]).strip()
        scope = cells[2].lower()
        if not target:
            continue
        if kind not in RELATION_TYPES:
            raise RegistryError(f"Unknown relationship type in source table: {kind}")
        if scope not in RELATION_SCOPES:
            raise RegistryError(f"Unknown relationship scope in source table: {scope}")
        result.append(validate_relationship({"type": kind, "scope": scope, "target": target}))
    return dedupe_relationships(result)


def validate_entry_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise RegistryError("entry metadata must be a JSON object")
    allowed = {
        "schema", "entry_id", "name", "aliases", "status", "relationships",
        "sources", "authority", "reason", "updated",
    }
    required = {
        "schema", "entry_id", "name", "aliases", "status",
        "relationships", "sources", "updated",
    }
    if set(value) - allowed or not required <= set(value):
        raise RegistryError("entry metadata has missing or unknown fields")
    if value["schema"] != 1:
        raise RegistryError("unsupported entry metadata schema")
    entry_id = validate_uuid(value["entry_id"], "entry_id")
    name = clean_field(value["name"], "canonical name", maximum=240)
    if not isinstance(value["aliases"], list):
        raise RegistryError("aliases must be a list")
    aliases = dedupe_strings(value["aliases"], "alias")
    if not isinstance(value["relationships"], list):
        raise RegistryError("relationships must be a list")
    relationships = dedupe_relationships(value["relationships"])
    if not isinstance(value["sources"], list):
        raise RegistryError("sources must be a list")
    sources = dedupe_strings(value["sources"], "source")
    placeholders = [source for source in sources if is_placeholder_source(source)]
    if placeholders:
        raise RegistryError(f"source is only a placeholder: {placeholders[0]}")
    status = clean_field(value["status"], "status")
    if status not in STATUSES:
        raise RegistryError(f"invalid canon status: {status}")
    result = {
        "schema": 1,
        "entry_id": entry_id,
        "name": name,
        "aliases": aliases,
        "status": status,
        "relationships": relationships,
        "sources": sources,
        "updated": parse_timestamp(value["updated"]),
    }
    if ("authority" in value) != ("reason" in value):
        raise RegistryError("authority and reason must be recorded together")
    for optional in ("authority", "reason"):
        if optional in value:
            result[optional] = clean_field(value[optional], optional, maximum=1000)
    return result


def strip_literal_regions(text: str) -> str:
    text = re.sub(r"```.*?```|~~~.*?~~~", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"`[^`\r\n]*`", "", text)
    return text


def visible_statuses(text: str) -> list[str]:
    return [match.strip() for match in VISIBLE_STATUS_RE.findall(strip_literal_regions(text))]


def mask_typed_relationship_wikis(
    text: str,
    relationships: list[dict[str, str]],
) -> str:
    """Mask only concrete typed wiki tokens backed one-for-one by metadata."""
    available: dict[tuple[str, str, str], int] = {}
    for relation in relationships:
        key = (relation["type"], relation["scope"], norm(relation["target"]))
        available[key] = available.get(key, 0) + 1
    section_match = re.search(
        r"^##[ \t]+Relationships[ \t]*\r?$\r?\n(.*?)(?=^##[ \t]+|\Z)",
        text,
        re.M | re.S | re.I,
    )
    if section_match is None:
        return text
    masked = list(text)
    body = section_match.group(1)
    body_start = section_match.start(1)
    for line_match in re.finditer(r"[^\r\n]*(?:\r?\n|\Z)", body):
        line = line_match.group(0).rstrip("\r\n")
        wiki_span: tuple[int, int] | None = None
        bullet = re.fullmatch(
            r"\s*[-*]\s*([a-z_]+)\s*:\s*(?:(internal|external|unresolved)\s*:)?\s*"
            r"(\[\[([^\]\r\n|#]+)\]\])\s*",
            line,
            re.I,
        )
        if bullet:
            kind = bullet.group(1).lower()
            scope = (bullet.group(2) or "internal").lower()
            target = bullet.group(4).strip()
            wiki_span = bullet.span(3)
        elif line.lstrip().startswith("|"):
            cell_matches = list(re.finditer(r"(?<=\|)[^|]*(?=\|)", line))
            if len(cell_matches) < 3:
                continue
            cells = [match.group(0).strip() for match in cell_matches]
            wiki = WIKI_RE.fullmatch(cells[1])
            if wiki is None:
                continue
            kind = cells[0].lower()
            scope = cells[2].lower()
            target = wiki.group(1).strip()
            raw_cell = cell_matches[1]
            relative = raw_cell.group(0).find(cells[1])
            wiki_span = (
                raw_cell.start() + relative,
                raw_cell.start() + relative + len(cells[1]),
            )
        else:
            continue
        key = (kind, scope, norm(target))
        if available.get(key, 0) < 1:
            continue
        available[key] -= 1
        if wiki_span is not None:
            start = body_start + line_match.start() + wiki_span[0]
            end = body_start + line_match.start() + wiki_span[1]
            masked[start:end] = " " * (end - start)
    return "".join(masked)


def extract_entry(path: Path) -> dict:
    text = read_utf8(path)
    if text.count(META_PREFIX) != 1:
        raise RegistryError(f"Entry requires exactly one metadata envelope: {path}")
    matches = list(META_LINE_RE.finditer(text))
    if len(matches) != 1 or matches[0].start() != 0:
        raise RegistryError(f"Entry requires exactly one leading metadata envelope: {path}")
    try:
        raw = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Malformed entry metadata in {path}: {exc}") from exc
    data = validate_entry_metadata(raw)
    body = text[matches[0].end():]
    titles = re.findall(r"^#[ \t]+(.+?)[ \t]*$", body, re.M)
    if len(titles) != 1:
        raise RegistryError(f"Entry requires exactly one H1 canonical name: {path}")
    title = clean_field(titles[0], "H1 canonical name", maximum=240)
    status_lines = visible_statuses(body)
    if norm(title) != norm(data["name"]):
        raise RegistryError(f"Metadata and H1 canonical names disagree: {path}")
    if len(status_lines) != 1:
        raise RegistryError(f"Entry requires exactly one visible canon status: {path}")
    if status_lines[0] != data["status"]:
        raise RegistryError(f"Metadata and visible canon status disagree: {path}")
    data["path"] = path
    data["text"] = text
    data["body"] = body
    return data


def load_entries(root: Path) -> list[dict]:
    directory = root / ENTRY_DIR
    if not directory.exists():
        return []
    if not directory.is_dir() or is_reparse(directory):
        raise RegistryError(f"Equipment directory is unsafe: {directory}")
    entries: list[dict] = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
        if path.name.startswith("_"):
            continue
        if is_reparse(path):
            raise RegistryError(f"Entry cannot be a symlink or junction: {path}")
        entries.append(extract_entry(path))
    return entries


def metadata_comment(meta: dict) -> str:
    public = {key: meta[key] for key in (
        "schema", "entry_id", "name", "aliases", "status", "relationships",
        "sources", "authority", "reason", "updated",
    ) if key in meta}
    payload = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if "-->" in payload or "\n" in payload or "\r" in payload:
        raise RegistryError("entry metadata cannot be serialized safely")
    return META_PREFIX + payload + " -->"


def render_entry(body: str, meta: dict) -> str:
    body = body.lstrip("\ufeff\r\n")
    status_line = f"**Canon status:** {meta['status']}"
    statuses = visible_statuses(body)
    if len(statuses) != 1:
        raise RegistryError("Source Markdown requires exactly one visible canon status")
    if statuses[0] != meta["status"]:
        raise RegistryError("Source visible canon status disagrees with requested status")
    body = VISIBLE_STATUS_RE.sub(lambda _: status_line, body, count=1)
    return f"{metadata_comment(meta)}\n{body.rstrip()}\n"


def markdown_cell(value: str) -> str:
    value = clean_field(value, "generated Markdown value", allow_empty=True, maximum=4000)
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "&#40;")
        .replace(")", "&#41;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_index(entries: list[dict]) -> str:
    lines = [
        "# Equipment Index", "",
        "| Canonical name | Status | Aliases | Relationships |",
        "|---|---|---|---|",
    ]
    for entry in sorted(entries, key=lambda item: (norm(item["name"]), item["entry_id"])):
        relative = entry["path"].name
        name = markdown_cell(entry["name"])
        relations = "; ".join(
            f"{markdown_cell(relation['type'])} [{markdown_cell(relation['scope'])}] "
            f"→ {markdown_cell(relation['target'])}"
            for relation in entry["relationships"]
        )
        aliases = ", ".join(markdown_cell(alias) for alias in entry["aliases"])
        lines.append(f"| [{name}]({relative}) | {entry['status']} | {aliases} | {relations} |")
    if not entries:
        lines.append("| _No entries_ | | | |")
    return "\n".join(lines) + "\n"


def render_status(entries: list[dict]) -> str:
    lines = ["# Equipment Canon Status", ""]
    for status in ("Established", "Proposed", "Speculative", "Contradicted", "Deprecated"):
        lines.extend([f"## {status}", ""])
        names = sorted((entry["name"] for entry in entries if entry["status"] == status), key=norm)
        lines.extend([f"- {markdown_cell(name)}" for name in names] or ["_None._"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_jsonl(root: Path, relative: str) -> list[dict]:
    path = safe_path(root, relative)
    if not path.exists():
        return []
    result: list[dict] = []
    for number, line in enumerate(read_utf8(path).splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RegistryError(f"Malformed JSONL at {relative}:{number}") from exc
        if not isinstance(value, dict):
            raise RegistryError(f"JSONL record must be an object at {relative}:{number}")
        result.append(value)
    return result


def validate_history_chain(records: list[dict], relative: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    previous = ""
    for index, record in enumerate(records, 1):
        if record.get("previous_sha256") != previous:
            findings.append(("ERROR", "history-chain-broken", f"{relative}:{index}"))
        payload = {key: value for key, value in record.items() if key != "record_sha256"}
        actual = sha256_bytes(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if record.get("record_sha256") != actual:
            findings.append(("ERROR", "history-record-tampered", f"{relative}:{index}"))
        previous = str(record.get("record_sha256", ""))
    return findings


def valid_history_record_keys(relative: str) -> set[str]:
    shared = {"previous_sha256", "record_sha256"}
    if relative == DECISIONS:
        return shared | {
            "schema", "timestamp", "entry_id", "entry", "action",
            "old_status", "new_status", "authority", "reason",
        }
    return shared | {
        "schema", "resolution_id", "timestamp", "entry", "claims", "sources",
        "authority", "reason", "decision", "superseded_interpretation",
        "affected_entries",
    }


def validate_history_records(records: list[dict], relative: str) -> list[tuple[str, str, str]]:
    findings = validate_history_chain(records, relative)
    required = valid_history_record_keys(relative)
    for index, record in enumerate(records, 1):
        if set(record) != required or record.get("schema") != 1:
            findings.append(("ERROR", "invalid-history-record", f"{relative}:{index}"))
        for key, value in record.items():
            if key in {"old_status"} and value is None:
                continue
            if key == "previous_sha256" and value == "":
                continue
            if key == "schema":
                continue
            try:
                clean_field(value, f"{relative}:{index}:{key}", maximum=4000)
            except RegistryError:
                findings.append(("ERROR", "invalid-history-field", f"{relative}:{index}:{key}"))
        try:
            parse_timestamp(record.get("timestamp"))
            if relative == DECISIONS:
                validate_uuid(record.get("entry_id"), f"{relative}:{index}:entry_id")
                if record.get("action") not in {"add", "update"}:
                    raise RegistryError("invalid decision action")
                if record.get("new_status") not in STATUSES:
                    raise RegistryError("invalid decision new status")
                if record.get("old_status") is not None and record.get("old_status") not in STATUSES:
                    raise RegistryError("invalid decision old status")
                if (record.get("action") == "add") != (record.get("old_status") is None):
                    raise RegistryError("decision action and old status disagree")
            else:
                validate_uuid(record.get("resolution_id"), f"{relative}:{index}:resolution_id")
            previous = str(record.get("previous_sha256", ""))
            if previous and not SHA_RE.fullmatch(previous):
                raise RegistryError("invalid previous history digest")
            if not SHA_RE.fullmatch(str(record.get("record_sha256", ""))):
                raise RegistryError("invalid history record digest")
        except RegistryError:
            findings.append(("ERROR", "invalid-history-record", f"{relative}:{index}"))
    return findings


def validate_decision_transitions(records: list[dict]) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    previous_by_entry: dict[str, dict] = {}
    for index, record in enumerate(records, 1):
        entry_id = record.get("entry_id")
        if not isinstance(entry_id, str):
            continue
        previous = previous_by_entry.get(entry_id)
        if previous is None:
            missing_protected_predecessor = (
                record.get("action") == "update"
                and record.get("old_status") in PROTECTED_STATUSES
            )
            invalid_creation = (
                record.get("action") == "add" and record.get("old_status") is not None
            )
            if missing_protected_predecessor or invalid_creation:
                findings.append(("ERROR", "history-missing-creation", f"{DECISIONS}:{index}"))
        elif previous.get("new_status") in PROTECTED_STATUSES and (
            record.get("action") != "update"
            or record.get("old_status") != previous.get("new_status")
        ):
            findings.append(("ERROR", "history-status-transition-broken", f"{DECISIONS}:{index}"))
        previous_by_entry[entry_id] = record
    return findings


def validate_history_anchor(
    records: list[dict],
    relative: str,
    marker: dict,
) -> list[tuple[str, str, str]]:
    expected = marker["history_anchors"][relative]
    actual = history_anchor(records)
    if actual != expected:
        return [("ERROR", "history-anchor-mismatch", relative)]
    return []


def validate_authority_evidence(
    entries: list[dict],
    decisions: list[dict],
) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for entry in entries:
        if entry["status"] not in PROTECTED_STATUSES and not (
            entry.get("authority") or entry.get("reason")
        ):
            continue
        matching_identity = [
            record for record in decisions if record.get("entry_id") == entry["entry_id"]
        ]
        latest = matching_identity[-1] if matching_identity else None
        if latest is None or not (
            latest.get("entry") == entry["name"]
            and latest.get("new_status") == entry["status"]
            and latest.get("authority") == entry.get("authority")
            and latest.get("reason") == entry.get("reason")
            and latest.get("timestamp") == entry["updated"]
        ):
            findings.append(("ERROR", "missing-authority-decision", entry["name"]))
    return findings


def validate_registry_histories(
    entries: list[dict],
    decisions: list[dict],
    resolutions: list[dict],
    marker: dict,
) -> list[tuple[str, str, str]]:
    findings = validate_history_records(decisions, DECISIONS)
    findings.extend(validate_history_records(resolutions, RESOLUTIONS))
    findings.extend(validate_decision_transitions(decisions))
    findings.extend(validate_history_anchor(decisions, DECISIONS, marker))
    findings.extend(validate_history_anchor(resolutions, RESOLUTIONS, marker))
    findings.extend(validate_authority_evidence(entries, decisions))
    return sorted(findings, key=finding_sort_key)


def chained_record(root: Path, relative: str, record: dict | None) -> dict | None:
    if record is None:
        return None
    existing = load_jsonl(root, relative)
    if validate_history_records(existing, relative):
        raise RegistryError(f"Cannot append to an invalid history chain: {relative}")
    previous = existing[-1]["record_sha256"] if existing else ""
    result = {**record, "previous_sha256": previous}
    result["record_sha256"] = sha256_bytes(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return result


def jsonl_append_text(root: Path, relative: str, record: dict | None) -> str:
    existing = safe_path(root, relative)
    text = read_utf8(existing) if existing.exists() else ""
    if record is None:
        return text
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.rstrip("\n") + ("\n" if text else "") + line + "\n"


def render_conflicts(entries: list[dict], resolutions: list[dict]) -> str:
    lines = [
        "# Equipment Canon Conflicts", "",
        "## Active conflicts", "",
        "| Entry | Conflict | Scope | Status |",
        "|---|---|---|---|",
    ]
    count = 0
    for entry in sorted(entries, key=lambda item: (norm(item["name"]), item["entry_id"])):
        for relation in entry["relationships"]:
            if relation["type"] == "contradicts":
                lines.append(
                    f"| {markdown_cell(entry['name'])} | contradicts "
                    f"{markdown_cell(relation['target'])} | {relation['scope']} | unresolved |"
                )
                count += 1
        if entry["status"] == "Contradicted" and not any(
            relation["type"] == "contradicts" for relation in entry["relationships"]
        ):
            lines.append(
                f"| {markdown_cell(entry['name'])} | Counterpart unspecified | unresolved | unresolved |"
            )
            count += 1
    if not count:
        lines.append("| _No explicit conflicts_ | | | |")
    lines.extend([
        "", "## Append-only resolution history", "",
        "| Timestamp | Entry | Authority | Decision | Superseded interpretation | Affected entries |",
        "|---|---|---|---|---|---|",
    ])
    for record in resolutions:
        lines.append(
            "| " + " | ".join(markdown_cell(str(record.get(key, ""))) for key in (
                "timestamp", "entry", "authority", "decision",
                "superseded_interpretation", "affected_entries",
            )) + " |"
        )
    if not resolutions:
        lines.append("| _No resolutions recorded_ | | | | | |")
    return "\n".join(lines) + "\n"


def registry_views(entries: list[dict], resolutions: list[dict]) -> dict[str, str]:
    return {
        f"{ENTRY_DIR}/_index.md": render_index(entries),
        "canon-status.md": render_status(entries),
        "conflicts.md": render_conflicts(entries, resolutions),
    }


def finding_sort_key(value: tuple[str, str, str]) -> tuple[str, str, str]:
    severity, code, detail = value
    return (severity, code, norm(detail))


def extract_markdown_destinations(text: str) -> list[str]:
    visible = strip_literal_regions(text)
    destinations: list[str] = []
    inline = re.compile(
        r"!?(?:\[[^\]\r\n]*\])\("
        r"\s*(?:<([^>\r\n]+)>|((?:\\.|[^\s()\\]|\((?:\\.|[^()\\])*\))+))"
        r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
    )
    for match in inline.finditer(visible):
        destinations.append(match.group(1) or match.group(2))
    definitions = re.compile(
        r"^[ \t]{0,3}\[[^\]\r\n]+\]:[ \t]*(?:<([^>\r\n]+)>|(\S+))",
        re.M,
    )
    for match in definitions.finditer(visible):
        destinations.append(match.group(1) or match.group(2))
    return destinations


def validate_entries(entries: list[dict]) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    names: dict[str, list[dict]] = {}
    aliases: dict[str, list[dict]] = {}
    for entry in entries:
        names.setdefault(norm(entry["name"]), []).append(entry)
        for alias in entry["aliases"]:
            aliases.setdefault(norm(alias), []).append(entry)
        if not entry["sources"]:
            findings.append(("ERROR", "missing-source", entry["name"]))
        if entry["status"] in PROTECTED_STATUSES and not (
            entry.get("authority") and entry.get("reason")
        ):
            findings.append(("ERROR", "missing-authority", entry["name"]))
    for values in names.values():
        if len(values) > 1:
            findings.append(("ERROR", "duplicate-name", ", ".join(item["name"] for item in values)))
    for key, owners in aliases.items():
        unique = {owner["entry_id"] for owner in owners}
        name_owners = {owner["entry_id"] for owner in names.get(key, [])}
        if len(unique) > 1 or (name_owners and unique != name_owners):
            findings.append(("ERROR", "duplicate-alias", f"{key}: {', '.join(o['name'] for o in owners)}"))
    resolvable: dict[str, dict] = {}
    for key, values in names.items():
        if len(values) == 1:
            resolvable[key] = values[0]
    for key, owners in aliases.items():
        if len({owner["entry_id"] for owner in owners}) == 1 and key not in resolvable:
            resolvable[key] = owners[0]
    inbound = {entry["entry_id"]: 0 for entry in entries}
    outgoing = {entry["entry_id"]: 0 for entry in entries}
    for entry in entries:
        literal_free = strip_literal_regions(mask_typed_relationship_wikis(
            entry["body"], entry["relationships"]
        ))
        typed_internal = {
            norm(relation["target"]): relation["target"]
            for relation in entry["relationships"] if relation["scope"] == "internal"
        }
        wiki_internal: dict[str, str] = {}
        for value in WIKI_RE.findall(literal_free):
            key = norm(value)
            wiki_internal[key] = value
        for key, display in sorted({**wiki_internal, **typed_internal}.items()):
            target = resolvable.get(key)
            if target is None:
                findings.append(("ERROR", "unresolved-link", f"{entry['name']} -> {display}"))
            elif target["entry_id"] != entry["entry_id"]:
                inbound[target["entry_id"]] += 1
                outgoing[entry["entry_id"]] += 1
        for destination in extract_markdown_destinations(entry["body"]):
            destination = unquote(destination.replace("\\", ""))
            split = urlsplit(destination)
            if split.scheme or split.netloc or destination.startswith("//"):
                continue
            path_part = split.path
            if not path_part:
                continue
            candidate = (entry["path"].parent / path_part).resolve(strict=False)
            try:
                candidate.relative_to(entry["path"].parent.parent.resolve())
            except ValueError:
                findings.append(("ERROR", "broken-link", f"{entry['name']} -> {destination}"))
                continue
            if not candidate.exists():
                findings.append(("ERROR", "broken-link", f"{entry['name']} -> {destination}"))
        if entry["status"] == "Contradicted" or any(
            relation["type"] == "contradicts" for relation in entry["relationships"]
        ):
            findings.append(("WARNING", "explicit-contradiction", entry["name"]))
    if len(entries) > 1:
        for entry in entries:
            if not outgoing[entry["entry_id"]] and not inbound[entry["entry_id"]]:
                findings.append(("WARNING", "orphan-entry", entry["name"]))
    return sorted(findings, key=finding_sort_key)


def blocking_findings(findings: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [finding for finding in findings if finding[0] == "ERROR"]


def path_identity(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RegistryError(f"Entry path escapes registry: {path}") from exc


def registry_revision(root: Path) -> str:
    parts: list[str] = []
    for relative in sorted(MANAGED_STATIC | {f"{ENTRY_DIR}/_index.md"}):
        path = safe_path(root, relative)
        parts.append(f"{relative}:{file_sha256(path) if path.is_file() else '-'}")
    directory = root / ENTRY_DIR
    if directory.is_dir():
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
            if not path.name.startswith("_"):
                parts.append(f"{path_identity(root, path)}:{file_sha256(path)}")
    return sha256_bytes("\n".join(parts).encode("utf-8"))


def safe_slug(name: str, existing_paths: set[str], identity: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    decomposed = "".join(character for character in decomposed if not unicodedata.combining(character))
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", decomposed).strip("-_. ").lower()
    if not base or base in WINDOWS_RESERVED:
        base = "equipment"
    base = base[:80].rstrip(" .-_") or "equipment"
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    candidate = f"{base}.md"
    reserved = norm(Path(candidate).stem) in WINDOWS_RESERVED
    if candidate.casefold() in existing_paths or reserved:
        candidate = f"{base[:67]}-{suffix}.md"
    if candidate.casefold() in existing_paths:
        raise RegistryError("Cannot allocate a collision-free deterministic entry path")
    return candidate


def changed_content(root: Path, changes: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative, content in changes.items():
        path = safe_path(root, relative)
        if not path.exists() or read_utf8(path) != content:
            result[relative] = content
    return result


def render_manifest(root: Path, action: str, semantic: dict, changes: dict[str, str]) -> dict:
    files: list[dict] = []
    for relative in sorted(changes):
        path = safe_path(root, relative)
        before_bytes = path.read_bytes() if path.exists() else b""
        after_bytes = changes[relative].encode("utf-8")
        before_text = before_bytes.decode("utf-8", errors="replace")
        after_text = changes[relative]
        status = "add" if not path.exists() else ("unchanged" if before_bytes == after_bytes else "modify")
        diff = "".join(difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="",
        ))
        files.append({
            "path": relative,
            "operation": status,
            "before_sha256": sha256_bytes(before_bytes) if path.exists() else None,
            "after_sha256": sha256_bytes(after_bytes),
            "diff": diff,
        })
    return {
        "schema": 1,
        "registry": str(root.resolve()),
        "action": action,
        "semantic": semantic,
        "validation": {"errors": 0},
        "files": files,
    }


def journal_record(root: Path, txn_name: str, changes: dict[str, str], registry_id: str) -> dict:
    txn = root / txn_name
    targets: list[dict] = []
    for relative, content in sorted(changes.items()):
        target = safe_path(root, relative)
        staged = txn / "stage" / PurePosixPath(relative)
        backup = txn / "backup" / PurePosixPath(relative)
        staged.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(staged, content)
        existed = target.is_file()
        record = {
            "path": relative,
            "existed": existed,
            "before_sha256": file_sha256(target) if existed else None,
            "after_sha256": sha256_bytes(content.encode("utf-8")),
            "backup_sha256": None,
        }
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            data = target.read_bytes()
            atomic_write_bytes(backup, data)
            record["backup_sha256"] = sha256_bytes(data)
        targets.append(record)
    return {
        "schema": JOURNAL_SCHEMA,
        "registry_id": registry_id,
        "txn_id": txn_name.removeprefix(".equipment-registry-txn-"),
        "txn_dir": txn_name,
        "state": "prepared",
        "targets": targets,
    }


def validate_journal(root: Path) -> tuple[dict, Path]:
    marker = load_marker(root)
    journal = read_json(root / JOURNAL)
    if set(journal) != {"schema", "registry_id", "txn_id", "txn_dir", "state", "targets"}:
        raise RegistryError("Transaction journal has missing or unknown fields")
    if journal["schema"] != JOURNAL_SCHEMA or journal["state"] != "prepared":
        raise RegistryError("Unsupported transaction journal schema or state")
    if validate_uuid(journal["registry_id"], "journal registry_id") != marker["registry_id"]:
        raise RegistryError("Transaction journal belongs to another registry")
    txn_id = clean_field(journal["txn_id"], "txn_id", maximum=64)
    if not re.fullmatch(r"[0-9a-f]{32}", txn_id):
        raise RegistryError("Invalid transaction ID")
    txn_dir = clean_field(journal["txn_dir"], "txn_dir", maximum=96)
    match = TXN_RE.fullmatch(txn_dir)
    if not match or match.group(1) != txn_id:
        raise RegistryError("Invalid transaction directory name")
    txn = root / txn_dir
    if not txn.is_dir() or is_reparse(txn):
        raise RegistryError("Transaction directory is missing or unsafe")
    if not isinstance(journal["targets"], list) or not journal["targets"]:
        raise RegistryError("Transaction journal requires nonempty targets")
    seen: set[str] = set()
    checked: list[dict] = []
    for raw in journal["targets"]:
        if not isinstance(raw, dict) or set(raw) != {
            "path", "existed", "before_sha256", "after_sha256", "backup_sha256",
        }:
            raise RegistryError("Invalid transaction target schema")
        relative = managed_relative(raw["path"])
        if relative in seen:
            raise RegistryError(f"Duplicate transaction target: {relative}")
        seen.add(relative)
        if not isinstance(raw["existed"], bool):
            raise RegistryError("Transaction existed field must be Boolean")
        for field in ("before_sha256", "after_sha256", "backup_sha256"):
            value = raw[field]
            if value is not None and (not isinstance(value, str) or not SHA_RE.fullmatch(value)):
                raise RegistryError(f"Invalid {field} in transaction journal")
        if not SHA_RE.fullmatch(str(raw["after_sha256"])):
            raise RegistryError("Missing staged-content hash")
        if raw["existed"] and (
            not SHA_RE.fullmatch(str(raw["before_sha256"]))
            or raw["backup_sha256"] != raw["before_sha256"]
        ):
            raise RegistryError("Existing target requires matching before and backup hashes")
        if not raw["existed"] and (
            raw["before_sha256"] is not None or raw["backup_sha256"] is not None
        ):
            raise RegistryError("New target cannot declare before or backup hashes")
        staged_relative = canonical_relative(f"stage/{relative}")
        staged = txn.joinpath(*PurePosixPath(staged_relative).parts)
        backup_relative = canonical_relative(f"backup/{relative}")
        backup = txn.joinpath(*PurePosixPath(backup_relative).parts)
        for candidate in (staged, backup):
            try:
                candidate.resolve(strict=False).relative_to(txn.resolve())
            except ValueError as exc:
                raise RegistryError("Transaction payload escapes transaction directory") from exc
            if candidate.exists() and is_reparse(candidate):
                raise RegistryError("Transaction payload cannot be a symlink or junction")
        if not staged.is_file() or file_sha256(staged) != raw["after_sha256"]:
            raise RegistryError(f"Staged content is missing or corrupt: {relative}")
        if raw["existed"] and (
            not backup.is_file() or file_sha256(backup) != raw["backup_sha256"]
        ):
            raise RegistryError(f"Backup is missing or corrupt: {relative}")
        checked.append({**raw, "path": relative})
    journal["targets"] = checked
    return journal, txn


def finish_transaction(root: Path, journal: dict, txn: Path) -> None:
    for item in journal["targets"]:
        target = safe_path(root, item["path"])
        staged = txn / "stage" / PurePosixPath(item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, staged.read_bytes())
    for item in journal["targets"]:
        target = safe_path(root, item["path"])
        if not target.is_file() or file_sha256(target) != item["after_sha256"]:
            raise RegistryError(f"Committed target failed integrity verification: {item['path']}")
    (root / JOURNAL).unlink()
    fsync_directory(root)
    try:
        shutil.rmtree(txn)
    except OSError:
        print(f"WARNING: committed transaction debris remains at {txn}", file=sys.stderr)


def rollback_transaction(root: Path, journal: dict, txn: Path) -> None:
    for item in journal["targets"]:
        target = safe_path(root, item["path"])
        current_hash = file_sha256(target) if target.is_file() else None
        permitted = {item["before_sha256"], item["after_sha256"]}
        if current_hash not in permitted:
            raise RegistryError(
                f"Target diverged from both transaction states; refusing recovery: {item['path']}"
            )
    for item in reversed(journal["targets"]):
        target = safe_path(root, item["path"])
        if item["existed"]:
            backup = txn / "backup" / PurePosixPath(item["path"])
            if not backup.is_file() or file_sha256(backup) != item["backup_sha256"]:
                raise RegistryError(f"Cannot restore verified backup: {item['path']}")
            atomic_write_bytes(target, backup.read_bytes())
        elif target.exists():
            if not target.is_file() or file_sha256(target) != item["after_sha256"]:
                raise RegistryError(f"Refusing to remove divergent new target: {item['path']}")
            target.unlink()
            fsync_directory(target.parent)
    for item in journal["targets"]:
        target = safe_path(root, item["path"])
        if item["existed"]:
            if not target.is_file() or file_sha256(target) != item["before_sha256"]:
                raise RegistryError(f"Recovery integrity check failed: {item['path']}")
        elif target.exists():
            raise RegistryError(f"Recovery failed to remove new target: {item['path']}")
    (root / JOURNAL).unlink()
    fsync_directory(root)
    try:
        shutil.rmtree(txn)
    except OSError:
        print(f"WARNING: recovered transaction debris remains at {txn}", file=sys.stderr)


def commit_transaction(root: Path, changes: dict[str, str]) -> None:
    if pending_transaction(root):
        raise RegistryError("Pending transaction requires explicit recover before any write")
    changes = changed_content(root, changes)
    if not changes:
        return
    marker = load_marker(root)
    txn_id = uuid.uuid4().hex
    txn_name = f".equipment-registry-txn-{txn_id}"
    txn = root / txn_name
    txn.mkdir(mode=0o700)
    journal_published = False
    try:
        journal = journal_record(root, txn_name, changes, marker["registry_id"])
        atomic_write(
            root / JOURNAL,
            json.dumps(journal, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        journal_published = True
        checked, checked_txn = validate_journal(root)
        finish_transaction(root, checked, checked_txn)
    except Exception as exc:
        if not journal_published:
            shutil.rmtree(txn, ignore_errors=False)
            raise RegistryError(f"Transaction failed before publication: {exc}") from exc
        raise RegistryError(
            f"Transaction interrupted; run explicit recover before continuing: {exc}"
        ) from exc


def command_recover(args: argparse.Namespace) -> int:
    root = require_registry(args.registry)
    if not pending_transaction(root):
        print("No pending transaction")
        return 0
    with RegistryLock(root):
        journal, txn = validate_journal(root)
        rollback_transaction(root, journal, txn)
    print("Recovered interrupted transaction to its verified prior state")
    return 0


def command_init(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    marker_path = root / MARKER
    exists = marker_path.exists()
    if root.exists() and not exists:
        raise RegistryError(
            f"Initialization target already exists but is not a registry: {root}"
        )
    if exists and not args.update:
        raise RegistryError(f"Registry already exists: {root}; use --update to rebuild views")
    if exists:
        marker = load_marker(root)
        if pending_transaction(root):
            raise RegistryError("Pending transaction requires explicit recover before rebuild")
        entries = load_entries(root)
        decisions = load_jsonl(root, DECISIONS)
        resolutions = load_jsonl(root, RESOLUTIONS)
        history_findings = validate_registry_histories(
            entries, decisions, resolutions, marker
        )
        if history_findings:
            raise RegistryError(
                "Cannot rebuild derived views with invalid append-only history: "
                + "; ".join(f"{code} {detail}" for _, code, detail in history_findings)
            )
        revision = registry_revision(root)
        changes = registry_views(entries, resolutions)
        findings = validate_entries(entries)
        errors = blocking_findings(findings)
        if errors:
            raise RegistryError(
                "Cannot rebuild derived views while registry has blocking findings: "
                + "; ".join(f"{code} {detail}" for _, code, detail in errors)
            )
        semantic = {
            "registry_id": marker["registry_id"],
            "created": marker["created"],
            "entry_count": len(entries),
        }
        manifest = render_manifest(root, "rebuild", semantic, changes)
        if args.dry_run:
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        with RegistryLock(root):
            if pending_transaction(root):
                raise RegistryError("Pending transaction appeared; recover and retry")
            if registry_revision(root) != revision:
                raise RegistryError("Registry changed after preview; retry from fresh state")
            commit_transaction(root, changes)
        print(f"Rebuilt equipment registry views: {root}")
        return 0
    created = parse_timestamp(args.timestamp or now())
    registry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"equipment-registry:{root}:{created}"))
    marker = {
        "schema": MARKER_SCHEMA,
        "kind": "equipment-canon-registry",
        "registry_id": registry_id,
        "created": created,
        "history_anchors": {
            DECISIONS: {"count": 0, "head_sha256": ""},
            RESOLUTIONS: {"count": 0, "head_sha256": ""},
        },
    }
    changes = {
        MARKER: marker_text(marker),
        "CHANGELOG.md": f"# Equipment Registry Changelog\n\n- {created} — Registry initialized.\n",
        DECISIONS: "",
        RESOLUTIONS: "",
        **registry_views([], []),
    }
    semantic = {"registry_id": registry_id, "created": created, "entry_count": 0}
    files = []
    for relative, content in sorted(changes.items()):
        data = content.encode("utf-8")
        files.append({
            "path": relative,
            "operation": "add",
            "before_sha256": None,
            "after_sha256": sha256_bytes(data),
            "diff": content,
        })
    manifest = {
        "schema": 1,
        "registry": str(root),
        "action": "initialize",
        "semantic": semantic,
        "validation": {"errors": 0},
        "files": files,
    }
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    root.mkdir(parents=True, exist_ok=False)
    try:
        for relative, content in changes.items():
            atomic_write(safe_path(root, relative), content)
        create_lock_file(root)
        print(f"Initialized equipment registry: {root}")
        return 0
    except Exception:
        if root.exists():
            shutil.rmtree(root)
        raise


def command_discover(args: argparse.Namespace) -> int:
    root = find_registry(Path(args.start))
    if root is None:
        raise RegistryNotFound("No equipment registry found")
    print(root)
    if pending_transaction(root):
        print("WARNING: pending transaction; run recover explicitly", file=sys.stderr)
    return 0


def protected_change(old: dict | None, status: str) -> bool:
    if status in PROTECTED_STATUSES:
        return True
    if old is None:
        return False
    if old["status"] in PROTECTED_STATUSES:
        return True
    return False


def validate_authority(args: argparse.Namespace, required: bool) -> tuple[str | None, str | None]:
    authority = clean_field(args.authority, "authority", maximum=300) if args.authority else None
    reason = clean_field(args.reason, "reason", maximum=1000) if args.reason else None
    confirmed = bool(args.confirm_canon_change)
    if required and not (authority and reason and confirmed):
        raise RegistryError(
            "Protected canon change requires --confirm-canon-change, --authority NAME, and --reason TEXT"
        )
    if any((authority, reason, confirmed)) and not all((authority, reason, confirmed)):
        raise RegistryError(
            "Authority record is incomplete; confirmation, authority, and reason must be supplied together"
        )
    return authority, reason


def make_resolution(args: argparse.Namespace, name: str, timestamp: str) -> dict | None:
    values = (
        args.resolve_decision,
        args.resolve_claims,
        args.resolve_sources,
        args.superseded_interpretation,
        args.affected_entries,
    )
    if not any(values):
        return None
    if not all(values):
        raise RegistryError(
            "Conflict resolution requires decision, claims, sources, superseded interpretation, "
            "and affected entries"
        )
    authority, reason = validate_authority(args, True)
    resolution_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        "|".join((
            name,
            timestamp,
            authority or "",
            args.resolve_decision or "",
            args.superseded_interpretation or "",
        )),
    ))
    return {
        "schema": 1,
        "resolution_id": resolution_id,
        "timestamp": timestamp,
        "entry": name,
        "claims": clean_field(args.resolve_claims, "resolution claims", maximum=2000),
        "sources": clean_field(args.resolve_sources, "resolution sources", maximum=2000),
        "authority": authority,
        "reason": reason,
        "decision": clean_field(args.resolve_decision, "resolution decision", maximum=2000),
        "superseded_interpretation": clean_field(
            args.superseded_interpretation,
            "superseded interpretation",
            maximum=2000,
        ),
        "affected_entries": clean_field(args.affected_entries, "affected entries", maximum=2000),
    }


def command_add(args: argparse.Namespace) -> int:
    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise RegistryError(f"Source Markdown file not found: {source_path}")
    root = require_registry(args.registry, Path.cwd())
    marker = load_marker(root)
    if pending_transaction(root):
        raise RegistryError("Pending transaction requires explicit recover before add or dry-run")
    text = read_utf8(source_path, allow_bom=True)
    if META_PREFIX in text:
        raise RegistryError("Source proposal must not contain registry metadata; import plain Markdown")
    titles = re.findall(r"^#[ \t]+(.+?)[ \t]*$", text, re.M)
    if len(titles) != 1:
        raise RegistryError("Source Markdown requires exactly one H1 canonical name")
    name = clean_field(titles[0], "canonical name", maximum=240)
    body = text.lstrip("\ufeff\r\n")
    status = args.status
    source_statuses = visible_statuses(body)
    if len(source_statuses) != 1:
        raise RegistryError("Source Markdown requires exactly one visible canon status")
    if source_statuses[0] != status:
        raise RegistryError("Source visible canon status disagrees with requested status")
    entries = load_entries(root)
    revision = registry_revision(root)
    decisions = load_jsonl(root, DECISIONS)
    resolutions = load_jsonl(root, RESOLUTIONS)
    history_findings = validate_registry_histories(
        entries, decisions, resolutions, marker
    )
    if history_findings:
        raise RegistryError(
            "Cannot mutate registry with invalid append-only history: "
            + "; ".join(f"{code} {detail}" for _, code, detail in history_findings)
        )
    matches = [entry for entry in entries if norm(entry["name"]) == norm(name)]
    if len(matches) > 1:
        raise RegistryError(f"Registry already contains duplicate canonical identity: {name}")
    old = matches[0] if matches else None
    if old is not None and not args.update:
        raise RegistryError(f"Entry {name!r} already exists; use --update for that identity")
    if old is None and args.update:
        raise RegistryError("--update can only target an existing normalized canonical identity")
    authority, reason = validate_authority(args, protected_change(old, status))
    timestamp = derive_timestamp(text, source_path, args.timestamp)
    aliases = parse_aliases(text) + list(args.alias or [])
    aliases = [alias for alias in dedupe_strings(aliases, "alias") if norm(alias) != norm(name)]
    relationships = dedupe_relationships(
        source_relationships(text) + list(args.relationship or [])
    )
    sources = parse_sources(text)
    if not sources:
        raise RegistryError("Candidate entry requires at least one non-placeholder source")
    existing_paths = {
        path_identity(root, entry["path"]).split("/", 1)[1].casefold()
        for entry in entries
        if old is None or entry["entry_id"] != old["entry_id"]
    }
    if old:
        path = old["path"]
        entry_id = old["entry_id"]
    else:
        entry_id = str(uuid.uuid5(uuid.UUID(marker["registry_id"]), norm(name)))
        filename = safe_slug(name, existing_paths, entry_id)
        path = safe_path(root, f"{ENTRY_DIR}/{filename}")
    meta = {
        "schema": 1,
        "entry_id": entry_id,
        "name": name,
        "aliases": aliases,
        "status": status,
        "relationships": relationships,
        "sources": sources,
        "updated": timestamp,
    }
    if authority:
        meta["authority"] = authority
        meta["reason"] = reason
    rendered = render_entry(body, meta)
    candidate = {**meta, "path": path, "text": rendered, "body": rendered.split("\n", 1)[1]}
    candidate_entries = [
        entry for entry in entries
        if old is None or entry["entry_id"] != old["entry_id"]
    ] + [candidate]
    findings = validate_entries(candidate_entries)
    errors = blocking_findings(findings)
    if errors:
        raise RegistryError(
            "Candidate registry failed preflight: "
            + "; ".join(f"{code} {detail}" for _, code, detail in errors)
        )
    resolution = make_resolution(args, name, timestamp)
    removed_conflicts = bool(old) and (
        old["status"] == "Contradicted" and status != "Contradicted"
        or {
            (relation["scope"], norm(relation["target"]))
            for relation in old["relationships"] if relation["type"] == "contradicts"
        } - {
            (relation["scope"], norm(relation["target"]))
            for relation in relationships if relation["type"] == "contradicts"
        }
    )
    if removed_conflicts and resolution is None:
        raise RegistryError(
            "Removing or resolving a recorded conflict requires a complete append-only resolution record"
        )
    if resolution:
        resolutions = resolutions + [resolution]
    action = "update" if old else "add"
    decision = None
    if authority:
        decision = {
            "schema": 1,
            "timestamp": timestamp,
            "entry_id": entry_id,
            "entry": name,
            "action": action,
            "old_status": old["status"] if old else None,
            "new_status": status,
            "authority": authority,
            "reason": reason,
        }
    changelog_path = root / "CHANGELOG.md"
    changelog = read_utf8(changelog_path) if changelog_path.exists() else "# Equipment Registry Changelog\n"
    past_tense = "updated" if action == "update" else "added"
    changelog_line = (
        f"- {timestamp} — {markdown_cell(name)} {past_tense} as {status}"
        + (f" by {markdown_cell(authority)}; reason: {markdown_cell(reason)}" if authority else "")
        + "."
    )
    changelog = changelog.rstrip() + "\n\n" + changelog_line + "\n"
    relative = path_identity(root, path)
    decision_record = chained_record(root, DECISIONS, decision)
    resolution_record = chained_record(root, RESOLUTIONS, resolution)
    candidate_decisions = decisions + ([decision_record] if decision_record else [])
    candidate_resolutions = load_jsonl(root, RESOLUTIONS) + (
        [resolution_record] if resolution_record else []
    )
    candidate_marker = marker_with_history_anchors(
        marker, candidate_decisions, candidate_resolutions
    )
    candidate_history_findings = validate_registry_histories(
        candidate_entries, candidate_decisions, candidate_resolutions, candidate_marker
    )
    if candidate_history_findings:
        raise RegistryError(
            "Candidate registry has invalid append-only history: "
            + "; ".join(f"{code} {detail}" for _, code, detail in candidate_history_findings)
        )
    if resolution_record:
        resolutions = candidate_resolutions
    changes = {
        relative: rendered,
        "CHANGELOG.md": changelog,
        MARKER: marker_text(candidate_marker),
        DECISIONS: jsonl_append_text(root, DECISIONS, decision_record),
        RESOLUTIONS: jsonl_append_text(root, RESOLUTIONS, resolution_record),
        **registry_views(candidate_entries, resolutions),
    }
    semantic = {
        "entry_id": entry_id,
        "canonical_name": name,
        "action": action,
        "old_status": old["status"] if old else None,
        "new_status": status,
        "authority": authority,
        "reason": reason,
        "aliases": aliases,
        "relationships": relationships,
        "warnings": [
            {"code": code, "detail": detail}
            for severity, code, detail in findings if severity == "WARNING"
        ],
    }
    manifest = render_manifest(root, action, semantic, changes)
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    with RegistryLock(root):
        if pending_transaction(root):
            raise RegistryError("Pending transaction appeared; recover and retry")
        if registry_revision(root) != revision:
            raise RegistryError("Registry changed after preview; retry from fresh state")
        commit_transaction(root, changes)
    print(f"{name} {past_tense}: {path}")
    return 0


def validate_registry(root: Path) -> list[tuple[str, str, str]]:
    marker = load_marker(root)
    entries = load_entries(root)
    findings = validate_entries(entries)
    decisions = load_jsonl(root, DECISIONS)
    resolutions = load_jsonl(root, RESOLUTIONS)
    findings.extend(validate_registry_histories(entries, decisions, resolutions, marker))
    expected = registry_views(entries, resolutions)
    for relative, content in expected.items():
        path = safe_path(root, relative)
        if not path.is_file() or read_utf8(path) != content:
            findings.append(("ERROR", "stale-derived-view", relative))
    return sorted(findings, key=finding_sort_key)


def command_validate(args: argparse.Namespace) -> int:
    root = require_registry(args.registry)
    if pending_transaction(root):
        print("ERROR [pending-transaction] run recover explicitly before trusting validation")
        return 1
    findings = validate_registry(root)
    for severity, code, detail in findings:
        print(f"{severity} [{code}] {detail}")
    errors = sum(severity == "ERROR" for severity, _, _ in findings)
    warnings = len(findings) - errors
    print(f"Validation complete: {errors} error(s), {warnings} warning(s)")
    return 1 if findings else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Maintain a fail-closed transactional fictional-equipment canon registry"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a registry or rebuild deterministic views")
    init.add_argument("path")
    init.add_argument("--update", action="store_true", help="rebuild views without changing provenance")
    init.add_argument("--dry-run", action="store_true", help="print a pure deterministic manifest")
    init.add_argument("--timestamp", help="ISO-8601 timestamp; recommended for reproducible initialization")
    init.set_defaults(func=command_init)

    discover = sub.add_parser("discover", help="find the nearest parent registry; never writes")
    discover.add_argument("start", nargs="?", default=".")
    discover.set_defaults(func=command_discover)

    recover_parser = sub.add_parser(
        "recover",
        help="explicitly roll back one verified interrupted transaction",
    )
    recover_parser.add_argument("--registry")
    recover_parser.set_defaults(func=command_recover)

    add = sub.add_parser("add", help="add or update one normalized canonical identity")
    add.add_argument("source")
    add.add_argument("--registry")
    add.add_argument("--status", choices=sorted(STATUSES), default="Proposed")
    add.add_argument("--alias", action="append")
    add.add_argument(
        "--relationship",
        action="append",
        type=parse_relationship,
        metavar="TYPE:[internal|external|unresolved]:TARGET",
        help=(
            "Typed relationship. Types: " + ", ".join(sorted(RELATION_TYPES))
            + ". Two-part TYPE:TARGET defaults to internal."
        ),
    )
    add.add_argument("--update", action="store_true")
    add.add_argument("--confirm-canon-change", action="store_true")
    add.add_argument("--authority")
    add.add_argument("--reason")
    add.add_argument(
        "--timestamp",
        help="ISO-8601 decision time; otherwise uses Last reviewed, then source file mtime",
    )
    add.add_argument("--dry-run", action="store_true", help="print a pure complete JSON manifest")
    add.add_argument("--resolve-decision")
    add.add_argument("--resolve-claims")
    add.add_argument("--resolve-sources")
    add.add_argument("--superseded-interpretation")
    add.add_argument("--affected-entries")
    add.set_defaults(func=command_add)

    check = sub.add_parser(
        "validate",
        help="read-only validation of schema, sources, identities, links, conflicts, and views",
    )
    check.add_argument("--registry")
    check.set_defaults(func=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except RegistryNotFound as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except (
        RegistryError,
        LockConflict,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
