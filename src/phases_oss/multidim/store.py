"""Persistence for the bundled Multidim store.

The store is a small JSON library of analysis contexts, kept on a **dedicated,
portable path** (see :mod:`phases_oss.multidim.paths`) -- never ``~/.multidim``.

Guarantees:

* **created on first run** from the neutral base contexts, without ever
  overwriting an existing valid store;
* a corrupt or older store is **backed up** (``store.json.bak``) before reset,
  never silently discarded;
* writes are **atomic** (temp file + replace) so a crash cannot truncate it;
* the personal ``~/.multidim`` store is never read, written, migrated or
  touched.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import paths
from .base_contexts import BASE_VERSION, SEED_KEYWORDS_VERSION, base_contexts

# Cross-process lock parameters for the read-modify-write of the store.
LOCK_TIMEOUT_S = 10.0    # give up acquiring after this long -> TimeoutError
LOCK_POLL_S = 0.05       # retry interval while the lock is held elsewhere

# Default filler phrases (v2 contract: the blacklist of hollow prose). Used by
# multidim_validate to measure the filler DENSITY of a section, never the mere
# presence of a single phrase. Stored at the store root so it survives restarts.
DEFAULT_GENERIC_BLACKLIST = [
    "it will need careful testing",
    "security is important",
    "it depends on the context",
    "we must remain vigilant",
    "a balanced approach is needed",
    "this deserves further thought",
    "to be monitored closely",
    "this deserves special attention",
]


def migrate_additive(store: Dict) -> bool:
    """ADDITIVE v2 migration: add missing fields without touching existing
    data, without a reset, without a version bump. Returns True when the store
    was completed (it must then be saved)."""
    changed = False
    # scrub markers PERSISTED by a pre-fix save(): deleting them here flags
    # the store for a locked re-save, which writes the clean copy. The
    # in-memory markers of a fresh reset are unaffected (set AFTER this runs).
    for k in PRIVATE_MARKERS:
        if k in store:
            del store[k]
            changed = True
    for c in store.get("contexts", []):
        if not isinstance(c, dict):
            continue
        # keywords/axes are RECOVERABLE gaps (a hand-edited store may omit
        # them): default them to [] instead of resetting the whole store.
        # Every consumer (detect_context, build_grid, learn) relies on the
        # keys existing; a missing 'name' stays unrecoverable -> reset path.
        if "keywords" not in c:
            c["keywords"] = []
            changed = True
        if "axes" not in c:
            c["axes"] = []
            changed = True
        if "traps" not in c:
            c["traps"] = []
            changed = True
        # a trap that omits 'active' would pass validity yet be silently
        # ignored by select_traps (which requires active is True); default it
        # to True on load, matching how learn writes every trap
        for t in c.get("traps", []):
            if isinstance(t, dict) and "active" not in t:
                t["active"] = True
                changed = True
    if "generic_blacklist" not in store:
        store["generic_blacklist"] = list(DEFAULT_GENERIC_BLACKLIST)
        changed = True
    if _merge_seed_keywords(store):
        changed = True
    return changed


def _merge_seed_keywords(store: Dict) -> bool:
    """Union keywords ADDED to a seed context into a store built by an older
    release. Additive only: nothing is renamed, reordered or removed, and a
    context the user created is never touched.

    Guarded by a stored marker rather than run on every load, so a seed keyword
    the user deliberately deletes afterwards does not silently come back (and
    the store is not rewritten on every start).
    """
    try:
        seen_version = int(store.get("seed_keywords_version", 0) or 0)
    except (ValueError, TypeError, OverflowError):
        seen_version = 0  # hand-edited garbage: redo the union, it is idempotent
    if seen_version >= SEED_KEYWORDS_VERSION:
        return False
    for seed in base_contexts():
        target = find_context(store, str(seed.get("name", "")))
        if target is None:
            continue  # a seed context the user deleted stays deleted
        keywords = target.get("keywords")
        if not isinstance(keywords, list):
            continue  # malformed: migrate_additive already defaulted it, skip
        present = {str(k).strip().casefold() for k in keywords if isinstance(k, str)}
        for k in seed.get("keywords", []):
            if str(k).strip().casefold() not in present:
                keywords.append(k)
                present.add(str(k).strip().casefold())
    store["seed_keywords_version"] = SEED_KEYWORDS_VERSION
    return True


def _fresh_store() -> Dict:
    store = {"version": BASE_VERSION, "contexts": base_contexts()}
    migrate_additive(store)
    return store


def _valid_context(c) -> bool:
    """A stored context must be an object whose ``axes`` are objects.

    This is the minimum shape the server relies on (``c.get(...)`` / ``a.get(...)``);
    a malformed store (e.g. a context that is a bare string) must be rejected so it
    is backed up and reset rather than crashing analysis later.
    """
    if not isinstance(c, dict):
        return False
    # every consumer indexes context["name"] (detection, frame building,
    # learn): a context without a non-empty name is unrecoverable -- there is
    # no name to invent -- so it must fail validation (backup + reset), never
    # crash multidim_analyze with a KeyError later
    name = c.get("name")
    if not isinstance(name, str) or not name.strip():
        return False
    if not isinstance(c.get("description", ""), str):
        return False
    keywords = c.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        return False
    axes = c.get("axes", [])
    if not isinstance(axes, list):
        return False
    for a in axes:
        if not isinstance(a, dict):
            return False
        if not isinstance(a.get("name", ""), str):
            return False
        if not isinstance(a.get("question", ""), str):
            return False
        subs = a.get("sublenses", [])
        if not isinstance(subs, list) or not all(isinstance(s, str) for s in subs):
            return False
    # traps, when present, must be a list of well-formed trap objects: a
    # malformed trap loaded from disk (e.g. triggers=1) would otherwise crash
    # frame building later, far from its cause. Reject it here so the store is
    # backed up and reset instead.
    if "traps" in c:
        traps = c.get("traps")
        if not isinstance(traps, list) or not all(_valid_trap(t) for t in traps):
            return False
    return True


def _valid_trap(t) -> bool:
    """Full trap shape the server relies on when building and validating v2
    frames. ``multidim_learn`` (sanitize_trap) always writes all of these; a
    trap missing any of them is hand-edited corruption. In particular a trap
    WITHOUT a trap_id would make TRAP_QUESTION_UNANSWERED impossible to
    satisfy: no answer could ever reference it."""
    if not isinstance(t, dict):
        return False
    for field in ("trap_id", "statement", "mandatory_question"):
        v = t.get(field)
        if not isinstance(v, str) or not v.strip():
            return False
    # the id is a lookup key: surrounding whitespace on disk would create
    # ghost duplicates that a single stripped answer satisfies at once
    if t["trap_id"] != t["trap_id"].strip():
        return False
    triggers = t.get("triggers")
    if (not isinstance(triggers, list) or not triggers
            or not all(isinstance(x, str) and x.strip() for x in triggers)):
        return False
    if "active" in t and not isinstance(t.get("active"), bool):
        return False
    return True


# A context name is a short IDENTIFIER (a word or linked keywords), never a
# sentence. Guards against a learn call where the DESCRIPTION was passed as
# the NAME, which would poison auto-detection and the context list.
MAX_CONTEXT_NAME_LEN = 40
MAX_CONTEXT_NAME_WORDS = 4


def validate_context_name(name) -> Optional[str]:
    """Return None when the name is an acceptable identifier, otherwise an
    actionable error message. Prevents a sentence from becoming a context name."""
    if not isinstance(name, str):
        return "the context name must be a string"
    n = name.strip()
    if not n:
        return "empty context name"
    if len(n) > MAX_CONTEXT_NAME_LEN:
        return ("context name too long ({} characters, max {}): it is a short "
                "identifier, not a sentence or a description"
                .format(len(n), MAX_CONTEXT_NAME_LEN))
    if len(n.split()) > MAX_CONTEXT_NAME_WORDS:
        return ("context name has {} words (max {}): use a short snake_case "
                "identifier (e.g. code_review), put the sentence in 'description'"
                .format(len(n.split()), MAX_CONTEXT_NAME_WORDS))
    # sentence punctuation is forbidden inside an identifier
    for ch in n:
        if ch in ".,;:!?()[]{}\"'":
            return ("context name contains punctuation ('{}'): use a short "
                    "snake_case identifier, put the sentence in 'description'"
                    .format(ch))
    return None


# Bounded retry for the final rename of an atomic write. On Windows,
# os.replace fails with PermissionError (sharing violation) while ANY other
# process holds the target open without FILE_SHARE_DELETE -- including our own
# lock-free fast-path read, an antivirus scan or a backup tool. Those holds
# are transient (milliseconds), so a short linear backoff absorbs them without
# changing the locking semantics; no application lock could cover external
# readers anyway. POSIX rename never fails for open readers, so the retry is
# never exercised there.
REPLACE_ATTEMPTS = 10
REPLACE_BACKOFF_S = 0.02   # linear: 0.02, 0.04, ... (~0.9 s worst case)


def _replace_with_retry(src: str, dst: str) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            # a PERSISTENT hold (file kept open, real ACL denial) still fails
            # closed with the original error after the last attempt
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_S * (attempt + 1))


def _atomic_write(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix="store-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        _replace_with_retry(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# In-memory diagnostics set by load() after a corrupt-store reset. They are
# for the CALLER only and must never reach the disk copy: a later mutate()
# would otherwise persist them forever (one even carries a machine-local
# path). An explicit tuple -- not a blanket '_' prefix filter -- so an
# unknown caller extension key is never silently dropped by save().
PRIVATE_MARKERS = ("_reset_reason", "_backup")


def save(store: Dict, path: Optional[Path] = None) -> None:
    target = path or paths.store_path()
    paths.assert_not_personal(target)  # even an explicit path can't touch ~/.multidim
    data = {k: v for k, v in store.items() if k not in PRIVATE_MARKERS}
    _atomic_write(target, data)


def _needs_no_migration(store: Dict) -> bool:
    """True when a validated store already has every additive v2 field, so it
    can be returned read-only without any write (the lock-free fast path)."""
    if "generic_blacklist" not in store:
        return False
    # a store built before the current seed keywords still needs the union:
    # returning it on the fast path would leave it routing decision subjects
    # to the generic grid forever
    try:
        if int(store.get("seed_keywords_version", 0) or 0) < SEED_KEYWORDS_VERSION:
            return False
    except (ValueError, TypeError, OverflowError):
        return False
    # markers PERSISTED by a pre-fix version of save() must not survive on the
    # lock-free fast path: falling through to the locked path scrubs them (the
    # single save there filters PRIVATE_MARKERS) instead of serving them forever
    if any(k in store for k in PRIVATE_MARKERS):
        return False
    for c in store.get("contexts", []):
        if not (isinstance(c, dict) and "traps" in c
                and "keywords" in c and "axes" in c):
            return False
        # a trap missing 'active' still needs the migration (it defaults the
        # field to True): returning it on the fast path would leave the trap
        # permanently invisible to select_traps, which requires active is True
        for t in c.get("traps", []):
            if isinstance(t, dict) and "active" not in t:
                return False
    return True


def _read_valid_no_migrate(path: Path) -> Optional[Dict]:
    """Lock-free fast path: return the store ONLY if it is present, valid, and
    needs no migration or reset (hence no write). Otherwise return None, so the
    caller falls back to the locked create/migrate/reset path. Never writes."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        version = int(parsed.get("version", 0))
    except (ValueError, TypeError, OverflowError):
        return None  # non-numeric / infinite version -> let the locked path handle it
    if version < BASE_VERSION:
        return None
    contexts = parsed.get("contexts")
    if not isinstance(contexts, list) or not all(_valid_context(c) for c in contexts):
        return None
    if not any(c.get("name") == "generic" for c in contexts):
        return None
    gb = parsed.get("generic_blacklist")
    if gb is not None and (not isinstance(gb, list) or not all(isinstance(x, str) for x in gb)):
        return None
    if not _needs_no_migration(parsed):
        return None
    return parsed


def _ensure_store_locked(path: Path):
    """Read + validate + IN-MEMORY additive migration. Returns
    ``(store, needs_persist)``. MUST be called while holding the store lock: its
    first-run seed and corrupt-store reset writes, and the migration the caller
    persists, all happen inside the locked critical section so a concurrent
    writer can never be clobbered.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Genuine first run: no file yet -> seed a neutral store.
        store = _fresh_store()
        save(store, path)
        return store, False
    # Any OTHER OSError (permission denied, is-a-directory, ...) means a store
    # exists but cannot be read: fail closed, never overwrite it.

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("store root is not an object")
        if int(parsed.get("version", 0)) < BASE_VERSION:
            raise ValueError("older store version")
        contexts = parsed.setdefault("contexts", [])
        if not isinstance(contexts, list) or not all(_valid_context(c) for c in contexts):
            raise ValueError("store contexts are malformed")
        if not any(c.get("name") == "generic" for c in contexts):
            raise ValueError("store is missing the required 'generic' context")
        if "generic_blacklist" in parsed:
            gb = parsed.get("generic_blacklist")
            # present but malformed (null, scalar, non-strings): the additive
            # migration would skip it (key exists) and frame building would
            # crash later -- reject here so the store is backed up and reset
            if not isinstance(gb, list) or not all(isinstance(x, str) for x in gb):
                raise ValueError("generic_blacklist is malformed")
        needs_persist = migrate_additive(parsed)  # in memory only
        return parsed, needs_persist
    except (ValueError, TypeError, OverflowError) as exc:
        # OverflowError: int() of an infinite JSON float version -> reset
        bak = path.with_name(path.name + ".bak")
        try:
            bak.write_text(raw, encoding="utf-8")
        except OSError as werr:
            # Fail closed: never destroy an unreadable store without a backup.
            # The reset below happens ONLY after the backup succeeded.
            raise RuntimeError(
                "store at %s is unreadable (%s) and could not be backed up (%s); "
                "refusing to overwrite it" % (path, exc, werr)
            )
        store = _fresh_store()
        save(store, path)
        store["_reset_reason"] = str(exc)
        store["_backup"] = str(bak)
        return store, False


def load(path: Optional[Path] = None) -> Dict:
    """Load the store, creating a neutral one on first run.

    * missing file  -> seed a fresh neutral store and persist it;
    * unreadable / older-version file -> back it up, then reset to neutral seeds
      (never a silent reset);
    * a valid current-version file is returned as-is (never overwritten).

    A valid, already-migrated store is returned on a lock-free fast path. Any
    write (first-run seed, additive migration, corrupt-store reset) happens
    under the lock, re-reading the disk after acquiring it, so a write can never
    clobber a concurrent writer's change.
    """
    path = path or paths.store_path()
    paths.assert_not_personal(path)  # even an explicit path can't touch ~/.multidim
    fast = _read_valid_no_migrate(path)
    if fast is not None:
        return fast
    lock_path = path.with_name(path.name + ".lock")
    with _file_lock(lock_path):
        store, needs_persist = _ensure_store_locked(path)  # re-read under lock
        if needs_persist:
            save(store, path)
        return store


def _try_native_lock(fd: int) -> bool:
    """Attempt a NON-BLOCKING exclusive OS lock on ``fd``.

    Returns True on success, False if another holder has it. Uses the kernel's
    own advisory lock (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows),
    which the OS releases automatically when the holder closes it OR crashes --
    so there is no stale lock to detect, no owner-liveness to guess, and no
    steal race. Independent file descriptors contend even within one process.
    """
    if sys.platform.startswith("win"):
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _native_unlock(fd: int) -> None:
    if sys.platform.startswith("win"):
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextlib.contextmanager
def _file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_S):
    """Cross-process lock serializing the store's read-modify-write.

    Backed by a NATIVE OS advisory lock on a dedicated lock file, so two servers
    sharing one store cannot clobber each other's write (last-writer-wins), and
    a crashed holder's lock is released by the kernel automatically -- no stale
    detection, no PID guessing, no steal race. Raises ``TimeoutError`` if the
    lock cannot be taken within ``timeout``.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + timeout
        while not _try_native_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "could not acquire the store lock at %s within %.1fs"
                    % (lock_path, timeout))
            time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            _native_unlock(fd)
    finally:
        os.close(fd)  # closing also drops the lock if still held (crash-safe)


def mutate(mutator: Callable[[Dict], object], path: Optional[Path] = None):
    """Serialize a read-modify-write of the store across processes.

    Under the file lock: RELOAD the store from disk (the source of truth, so a
    concurrent writer's changes are not lost), run ``mutator(fresh)`` in place,
    persist, then release. Returns ``(fresh_store, mutator_result)`` so the
    caller can refresh its in-memory copy only on success. A failure in the
    mutator or in ``save`` propagates with the on-disk store left as it was
    before this call's save (the reload+save is the whole critical section).
    """
    path = path or paths.store_path()
    paths.assert_not_personal(path)
    lock_path = path.with_name(path.name + ".lock")
    with _file_lock(lock_path):
        # use _ensure_store_locked, NOT load(): load() would recurse back into
        # the lock. It does the in-memory seed/reset/migration; our single save
        # below persists that plus the mutation, atomically, under the lock.
        fresh, _needs_persist = _ensure_store_locked(path)  # disk truth, locked
        result = mutator(fresh)                              # mutate in place
        save(fresh, path)                                   # persist merged result
        return fresh, result


def find_context(store: Dict, name: str) -> Optional[Dict]:
    low = name.lower()
    for c in store.get("contexts", []):
        if str(c.get("name", "")).lower() == low:
            return c
    return None


def list_context_names(store: Dict) -> List[str]:
    return [str(c.get("name", "")) for c in store.get("contexts", [])]
