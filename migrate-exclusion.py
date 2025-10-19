from __future__ import annotations

import argparse
import string
from pathlib import Path
from typing import Iterable, List, Tuple, Dict

from brln_orchestrator.storage import Storage

DEFAULT_DB = "brln_orchestrator.sqlite3"


def resolve_db_path(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return (Path.cwd() / DEFAULT_DB).resolve()


def _extract_block(text: str, marker: str, open_char: str, close_char: str) -> str | None:
    start = text.find(marker)
    if start == -1:
        return None
    open_idx = text.find(open_char, start)
    if open_idx == -1:
        return None
    depth = 0
    for idx in range(open_idx, len(text)):
        char = text[idx]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : idx]
    return None


def _parse_entries(block: str) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        note = ""
        if "#" in line:
            value_part, note_part = line.split("#", 1)
            line = value_part.strip()
            note = note_part.strip()
        identifier = normalize_identifier(line)
        if identifier:
            entries.append((identifier, note))
    return entries


def load_pubkey_exclusions(path: Path) -> List[Tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    block = _extract_block(text, "EXCLUSION_LIST", "{", "}")
    if not block:
        return []
    return _parse_entries(block)


def load_channel_exclusions(path: Path) -> List[Tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    block = _extract_block(text, "EXCLUSION_LIST", "[", "]")
    if not block:
        return []
    return _parse_entries(block)


def load_forced_sources(path: Path) -> List[Tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    block = _extract_block(text, "FORCE_SOURCE_LIST", "[", "]")
    if not block:
        return []
    return _parse_entries(block)


def normalize_identifier(identifier: str) -> str:
    value = identifier.strip()
    while value.endswith(","):
        value = value[:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()


def deduplicate(items: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen: Dict[str, str] = {}
    for identifier, note in items:
        key = normalize_identifier(identifier)
        if not key:
            continue
        if key not in seen or (note and not seen[key]):
            seen[key] = note
    return [(key, value) for key, value in seen.items()]


def _is_pubkey(identifier: str) -> bool:
    return len(identifier) == 66 and all(ch in string.hexdigits for ch in identifier)


def cleanup_exclusions(storage: Storage) -> None:
    current = storage.list_exclusions()
    for identifier, note in list(current.items()):
        normalized = normalize_identifier(identifier)
        if not normalized:
            storage.remove_exclusion(identifier)
            continue
        if normalized != identifier:
            storage.remove_exclusion(identifier)
            storage.set_exclusion(normalized, note)


def cleanup_forced_sources(storage: Storage) -> None:
    current = storage.list_forced_sources()
    for identifier, note in list(current.items()):
        normalized = normalize_identifier(identifier)
        if not normalized:
            storage.remove_forced_source(identifier)
            continue
        if normalized != identifier:
            storage.remove_forced_source(identifier)
            storage.set_forced_source(normalized, note)


def migrate(
    db_path: Path,
    pubkey_entries: List[Tuple[str, str]],
    chan_entries: List[Tuple[str, str]],
    forced_entries: List[Tuple[str, str]],
) -> Tuple[int, int, int]:
    combined = deduplicate(pubkey_entries + chan_entries)
    storage = Storage(db_path)
    inserted_pubkeys = 0
    inserted_channels = 0
    inserted_forced = 0
    try:
        cleanup_exclusions(storage)
        cleanup_forced_sources(storage)
        for identifier, note in combined:
            storage.set_exclusion(identifier, note or None)
        for identifier, _ in combined:
            if _is_pubkey(identifier):
                inserted_pubkeys += 1
            if identifier.isdigit():
                inserted_channels += 1
        for identifier, note in deduplicate(forced_entries):
            if not identifier.isdigit():
                continue
            storage.set_forced_source(identifier, note or None)
            inserted_forced += 1
    finally:
        storage.close()
    return inserted_pubkeys, inserted_channels, inserted_forced


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra exclusões dos scripts legados para a base SQLite do orquestrador.")
    parser.add_argument("--db", dest="db_path", help="Caminho para o arquivo SQLite do orquestrador.")
    parser.add_argument("--autofee", default="brln-autofee.py", help="Path do script legado do AutoFee.")
    parser.add_argument("--ar", default="lndg_AR_trigger.py", help="Path do script legado do AR Trigger.")
    args = parser.parse_args()

    db_path = resolve_db_path(getattr(args, "db_path", None))
    autofee_path = Path(args.autofee)
    ar_path = Path(args.ar)

    if not autofee_path.exists():
        raise FileNotFoundError(f"Arquivo AutoFee não encontrado: {autofee_path}")
    if not ar_path.exists():
        raise FileNotFoundError(f"Arquivo AR Trigger não encontrado: {ar_path}")

    pubkey_entries = load_pubkey_exclusions(autofee_path)
    chan_entries = load_channel_exclusions(ar_path)
    forced_entries = load_forced_sources(ar_path)

    pubkey_count, channel_count, forced_count = migrate(db_path, pubkey_entries, chan_entries, forced_entries)

    print(f"[ok] {pubkey_count} exclusoes de pubkey migradas.")
    print(f"[ok] {channel_count} exclusoes de channel id migradas.")
    print(f"[ok] {forced_count} canais forçados como source migrados.")
    print(f"[ok] arquivo SQLite atualizado em {db_path}")


if __name__ == "__main__":
    main()
