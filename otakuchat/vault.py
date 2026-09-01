"""The vault — a RAG-style dump directory the user drops text/code/markdown
into (by hand, or via /import pulling a git repo, zip, or single file from
a URL), which then auto-grounds chat turns the same way memory/facts/
snippets do: relevance-ranked against the current message, only the
matching chunks ever enter a turn.

Two directories, two lifetimes:

- **vault** (config.get_vault_path(), default
  ~/.local/share/otakuchat/vault) — the wipeable dump. Anything imported
  or hand-placed here is fair game for /vault's Wipe button.
- **seed** (config.get_seed_path(), default
  ~/.local/share/otakuchat/seed) — a whitelist directory Wipe never
  touches. "Add to seed" copies a vault file here so it survives a wipe;
  files placed directly in seed by hand work the same way.

Retrieval indexes BOTH directories together (a seeded reference doc is
just as useful for grounding as a freshly-dumped one) — the vault/seed
split is purely a lifecycle distinction (what Wipe can delete), not a
retrieval-scope distinction.

Adapted from Otakumafia's hutuio project (importer.py's git-clone/zip-
extract/single-file smart importer), ported to a headless (no DirectoryTree
picker needed — see vault_ui.py for the fuzzy list/remove/seed UI) import
flow that always targets the vault directory, plus a chunked-retrieval
layer hutuio never had (hutuio is a CMS, not a chat app with a system
prompt to ground).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import config, db
from .redact import redact_secrets
from .threats import scan_for_threats

USER_AGENT = "Mozilla/5.0 (compatible; OtakuChat/0.1; +https://github.com/wolfercarter-cpu/otaku-chat)"


class ImportError_(Exception):
    """Distinct name from the builtin ImportError so a real Python import
    failure elsewhere never gets mistaken for a vault-import failure."""


@dataclass
class VaultEntry:
    """One file under vault/ or seed/, as surfaced to vault_ui.py's fuzzy
    list — `relpath` is relative to its own root (vault or seed), `root`
    distinguishes which."""
    root: str  # "vault" | "seed"
    relpath: str
    abspath: Path
    size: int
    indexed: bool  # False for binaries/oversized/unindexed-extension files


# --- paths ------------------------------------------------------------

def vault_dir() -> Path:
    return Path(config.get_vault_path())


def seed_dir() -> Path:
    return Path(config.get_seed_path())


def _is_within(path: Path, root: Path) -> bool:
    """True if `path` resolves to somewhere under `root` — guards every
    delete/move against a path that escaped its intended root (symlink,
    '..' component, or a caller bug), since Wipe and Remove are
    destructive."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# --- listing ------------------------------------------------------------

def list_entries(root: str = "vault") -> list[VaultEntry]:
    """List every file under vault/ or seed/, recursively. Directories
    themselves aren't listed — /vault's fuzzy picker operates on files."""
    base = vault_dir() if root == "vault" else seed_dir()
    if not base.exists():
        return []

    indexed_exts = config.get_vault_indexed_extensions()
    entries = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(base).parts):
            continue  # skip dotfiles/dotdirs (.git, .cache, ...) same as FileBrowser
        try:
            size = p.stat().st_size
        except OSError:
            continue
        entries.append(VaultEntry(
            root=root,
            relpath=str(p.relative_to(base)),
            abspath=p,
            size=size,
            indexed=p.suffix.lower() in indexed_exts,
        ))
    return entries


def list_all_entries() -> list[VaultEntry]:
    return list_entries("vault") + list_entries("seed")


# --- remove / seed / wipe -------------------------------------------------

def remove_entry(root: str, relpath: str) -> bool:
    """Delete one file from vault/ or seed/. Returns True if actually
    removed. Also drops its chunk-index rows so a removed file never
    surfaces in retrieval after deletion."""
    base = vault_dir() if root == "vault" else seed_dir()
    target = (base / relpath)
    if not _is_within(target, base) or not target.is_file():
        return False
    try:
        target.unlink()
    except OSError:
        return False
    db.delete_vault_chunks_for_path(root, relpath)
    return True


def add_to_seed(relpath: str) -> bool:
    """Copy a vault file into seed/ (creating parent dirs as needed) so it
    survives wipe_vault(). Returns True on success. Does not remove the
    original from vault — seeding is additive, Wipe is what clears vault."""
    src = vault_dir() / relpath
    if not _is_within(src, vault_dir()) or not src.is_file():
        return False
    dest = seed_dir() / relpath
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError:
        return False
    index_file("seed", relpath, dest)
    return True


def wipe_vault() -> int:
    """Delete every file under vault/ (never touches seed/ — that's the
    whole point of seeding something first). Returns the number of files
    removed."""
    entries = list_entries("vault")
    removed = 0
    for entry in entries:
        if remove_entry("vault", entry.relpath):
            removed += 1
    return removed


# --- indexing (chunking for retrieval) -----------------------------------

def index_file(root: str, relpath: str, abspath: Path) -> None:
    """(Re)index one file's text into db's vault_chunks table for
    relevance retrieval. Binaries and unindexed extensions are skipped —
    they still exist on disk (git clone/zip extract need that), just
    never chunked/searched since we can't usefully token-overlap-rank
    binary content."""
    db.delete_vault_chunks_for_path(root, relpath)

    indexed_exts = config.get_vault_indexed_extensions()
    if abspath.suffix.lower() not in indexed_exts:
        return

    try:
        text = abspath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return  # not actually text despite the extension — skip silently

    max_chars = config.get_vault_max_file_index_chars()
    text = text[:max_chars]
    if not text.strip():
        return

    text, _ = redact_secrets(text)  # imported content is untrusted, same as /add
    if scan_for_threats(text):
        # Indexed vault chunks get replayed straight into a system
        # message every relevant turn — same exposure as a curated
        # memory/fact/snippet. A repo file with an injection payload
        # (a "SYSTEM: ignore previous instructions" README, a poisoned
        # code comment) stays on disk untouched — git clone/zip extract
        # still succeed — it's just never chunked into the index.
        return
    db.add_vault_chunk(root, relpath, text)


def reindex_all() -> int:
    """Re-scan vault/ and seed/ and rebuild the chunk index from scratch.
    Returns the count of files (re)indexed. Call after a bulk hand-edit
    of the directories outside the app (the app itself indexes
    incrementally on import/seed)."""
    db.clear_vault_chunks()
    count = 0
    for entry in list_all_entries():
        if entry.indexed:
            index_file(entry.root, entry.relpath, entry.abspath)
            count += 1
    return count


# --- retrieval ------------------------------------------------------------

def render_vault_block(query: str, max_items: int | None = None) -> str:
    """Render only the vault chunks actually relevant to `query` — never
    the whole vault. Strictly relevance-gated (see db.relevant_vault_
    chunks), same reasoning as facts.py/snippets.py: imported reference
    material should never get replayed into a turn it has nothing to do
    with just because the vault isn't empty."""
    if db.count_vault_chunks() == 0:
        return ""
    if not query or not query.strip():
        return ""

    limit = max_items or config.get_vault_results_per_turn()
    matches = db.relevant_vault_chunks(query, limit=limit)
    if not matches:
        return ""

    max_chunk_chars = config.get_vault_max_chunk_chars()
    parts = []
    for m in matches:
        text = m["content"][:max_chunk_chars]
        parts.append(f"## {m['relpath']} ({m['root']})\n{text}")
    return "## Relevant vault content\n\n" + "\n\n".join(parts) + "\n"


# --- import (git clone / zip extract / single file) -----------------------

def import_url(target_url: str) -> str:
    """Smart importer, adapted from hutuio's importer.py: always targets
    vault_dir() (no destination picker — the vault has exactly one
    place things land). Returns a human-readable success message.
    Raises ImportError_ on any failure — best-effort, callers surface the
    message via notify() rather than crashing a turn/UI action.

    - a URL ending in .git: `git clone` into vault/
    - a URL ending in .zip: download + extract into vault/
    - anything else: download as a single file into vault/, named from
      the URL's path
    """
    target_url = target_url.strip()
    if not target_url:
        raise ImportError_("empty URL")
    parsed = urllib.parse.urlparse(target_url)
    if parsed.scheme not in ("http", "https"):
        raise ImportError_("only http(s) URLs are supported")

    dest_root = vault_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    timeout = config.get_vault_import_timeout()

    if target_url.endswith(".git"):
        return _import_git_repo(target_url, dest_root, timeout)
    elif target_url.lower().endswith(".zip"):
        return _import_zip(target_url, dest_root, timeout)
    else:
        return _import_single_file(target_url, parsed, dest_root, timeout)


def _import_git_repo(target_url: str, dest_root: Path, timeout: int) -> str:
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", target_url],
            cwd=str(dest_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ImportError_(f"git clone timed out after {timeout}s")
    except FileNotFoundError:
        raise ImportError_("git is not installed")
    if result.returncode != 0:
        raise ImportError_(f"git clone failed: {result.stderr.strip()[:300]}")

    repo_name = target_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    repo_path = dest_root / repo_name
    indexed = _index_new_tree(repo_path, dest_root)
    return f"Cloned '{repo_name}' — {indexed} file(s) indexed for retrieval."


def _import_zip(target_url: str, dest_root: Path, timeout: int) -> str:
    fd, temp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        _download_file(target_url, temp_path, timeout)
        with zipfile.ZipFile(temp_path, "r") as zf:
            # Guard against zip-slip: refuse any member path that would
            # extract outside dest_root (a malicious/corrupt zip entry
            # using '..' or an absolute path).
            for member in zf.namelist():
                member_path = (dest_root / member).resolve()
                if not _is_within(member_path, dest_root):
                    raise ImportError_(f"zip archive contains an unsafe path: {member}")
            zf.extractall(dest_root)
    except zipfile.BadZipFile:
        raise ImportError_("downloaded file is not a valid zip archive")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    indexed = _index_new_tree(dest_root, dest_root)
    return f"Zip archive extracted — {indexed} file(s) indexed for retrieval."


def _import_single_file(target_url: str, parsed, dest_root: Path, timeout: int) -> str:
    filename = Path(parsed.path).name or "downloaded_file"
    # Reject a filename that would escape dest_root via path traversal
    # in the URL itself (e.g. "../../etc/passwd" as the path component).
    destination = (dest_root / filename)
    if not _is_within(destination, dest_root):
        raise ImportError_("unsafe filename in URL")

    _download_file(target_url, destination, timeout)
    index_file("vault", filename, destination)
    indexed = "indexed" if destination.suffix.lower() in config.get_vault_indexed_extensions() else "stored (not indexed — extension not in VAULT.indexed_extensions)"
    return f"Downloaded '{filename}' — {indexed}."


def _download_file(url: str, destination: str | Path, timeout: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(destination, "wb") as out:
                out.write(resp.read())
    except urllib.error.HTTPError as e:
        raise ImportError_(f"HTTP {e.code} downloading {url}")
    except (urllib.error.URLError, OSError) as e:
        raise ImportError_(f"could not download {url}: {e}")


def _index_new_tree(tree_root: Path, vault_root: Path) -> int:
    """Index every indexable file under `tree_root` (a freshly-cloned
    repo or extracted zip, somewhere under vault_root) into the chunk
    store. Returns the count indexed."""
    indexed_exts = config.get_vault_indexed_extensions()
    count = 0
    for p in tree_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(vault_root).parts):
            continue
        if p.suffix.lower() not in indexed_exts:
            continue
        relpath = str(p.relative_to(vault_root))
        index_file("vault", relpath, p)
        count += 1
    return count
