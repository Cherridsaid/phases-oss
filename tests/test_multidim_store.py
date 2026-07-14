"""Tests for the bundled Multidim store: dedicated path, no overwrite, neutral.

The store must live on a dedicated, portable path (never ``~/.multidim``), be
created on first run, never overwrite an existing valid store, back up a corrupt
one before reset, and ship strictly neutral base contexts.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from phases_oss.multidim import base_contexts, paths, store


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-mdstore-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()


class TestStorePath(StoreTestBase):
    def test_path_is_under_override(self):
        p = paths.store_path()
        self.assertTrue(str(p).startswith(self._tmp.name))
        self.assertTrue(str(p).endswith(os.path.join("multidim", "store.json")))

    def test_never_resolves_to_personal_multidim(self):
        # With no override, the resolved path must never be ~/.multidim.
        os.environ.pop("PHASES_OSS_HOME", None)
        personal = Path(os.path.expanduser(os.path.join("~", ".multidim"))).resolve()
        resolved_parent = paths.store_path().resolve().parent
        self.assertNotEqual(resolved_parent, personal)

    def test_explicit_path_into_personal_is_refused_on_load_and_save(self):
        # save()/load() with an explicit path must also refuse ~/.multidim,
        # not only the default store_path().
        target = Path(os.path.expanduser(os.path.join("~", ".multidim", "store.json")))
        with self.assertRaises(RuntimeError):
            store.save({"version": base_contexts.BASE_VERSION, "contexts": []}, target)
        with self.assertRaises(RuntimeError):
            store.load(target)

    def test_override_into_personal_multidim_is_refused(self):
        # Even an explicit override equal to OR beneath ~/.multidim is refused,
        # so the personal store can never be written under any configuration.
        personal = os.path.expanduser(os.path.join("~", ".multidim"))
        for target in (personal, os.path.join(personal, "sub", "deeper")):
            with self.subTest(target=target):
                os.environ["PHASES_OSS_HOME"] = target
                with self.assertRaises(RuntimeError):
                    paths.store_path()


class TestFirstRunAndPersistence(StoreTestBase):
    def test_first_run_seeds_neutral_store(self):
        p = paths.store_path()
        self.assertFalse(p.exists())
        s = store.load()
        self.assertTrue(p.exists())
        self.assertEqual(s["version"], base_contexts.BASE_VERSION)
        names = store.list_context_names(s)
        self.assertIn("generic", names)
        self.assertGreaterEqual(len(names), 2)

    def test_existing_valid_store_is_not_overwritten(self):
        p = paths.store_path()
        store.load()  # seed
        # Add a user context, save, reload: it must survive (no overwrite).
        s = store.load()
        s["contexts"].append({"name": "mine", "description": "d", "keywords": [], "axes": []})
        store.save(s)
        reloaded = store.load()
        self.assertIn("mine", store.list_context_names(reloaded))

    def test_corrupt_store_backed_up_then_reset(self):
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ this is not json", encoding="utf-8")
        s = store.load()
        # backup created, store reset to neutral seeds (never silently discarded)
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(s))
        self.assertEqual(s["version"], base_contexts.BASE_VERSION)

    def test_older_version_backed_up_then_reset(self):
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 0, "contexts": []}), encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertEqual(s["version"], base_contexts.BASE_VERSION)

    def test_non_numeric_version_backed_up_then_reset(self):
        # a non-numeric version must not crash the fast path: it falls through
        # to the locked path, which backs up and resets
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": "x", "contexts": []}), encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertEqual(s["version"], base_contexts.BASE_VERSION)

    def test_infinite_version_backed_up_then_reset(self):
        # an infinite JSON float version (1e999) must not crash int(): the store
        # is backed up and reset like any other unreadable one
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"version": 1e999, "contexts": []}', encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertEqual(s["version"], base_contexts.BASE_VERSION)

    def test_missing_generic_backed_up_then_reset(self):
        # A valid-looking store without the required 'generic' context must be
        # reset, so analysis never fails later for a missing fallback.
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": base_contexts.BASE_VERSION, "contexts": []}),
                     encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(s))

    def test_context_with_non_list_keywords_is_malformed(self):
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": base_contexts.BASE_VERSION,
                                 "contexts": [{"name": "generic", "keywords": 1, "axes": []}]}),
                     encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(s))

    def test_deeply_malformed_context_fields_are_rejected(self):
        # Non-string keyword/sublens entries, non-string axis fields: all rejected
        # so analysis never hits a type error later. One comprehensive guard.
        bad_variants = [
            {"name": "generic", "keywords": [1], "axes": []},
            {"name": "generic", "keywords": [], "axes": [{"name": "A", "sublenses": [2]}]},
            {"name": "generic", "keywords": [], "axes": [{"name": 5, "sublenses": []}]},
            {"name": "generic", "keywords": [], "axes": ["not-an-axis"]},
        ]
        for i, ctx in enumerate(bad_variants):
            with self.subTest(i=i):
                self.assertFalse(store._valid_context(ctx))

    def test_malformed_contexts_backed_up_then_reset(self):
        # A store whose contexts are malformed (e.g. a bare string) must be
        # rejected and reset, not returned to crash analysis later.
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": base_contexts.BASE_VERSION,
                                 "contexts": ["bad"]}), encoding="utf-8")
        s = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(s))

    def test_unreadable_store_is_not_overwritten(self):
        # A store that exists but cannot be read (here: a directory in its place)
        # must NOT be treated as "first run" and overwritten. Fail closed.
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir()  # store.json is now a directory -> read raises a non-FNF OSError
        with self.assertRaises(OSError):
            store.load()
        self.assertTrue(p.is_dir())  # untouched, not replaced by a seeded file

    def test_corrupt_store_without_backup_raises_and_keeps_data(self):
        # If the backup cannot be written, the corrupt store must NOT be
        # overwritten: fail closed (raise), never destroy data silently.
        p = paths.store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        original = "{ corrupt but precious"
        p.write_text(original, encoding="utf-8")
        # Make the backup path unwritable by turning it into a directory.
        p.with_name(p.name + ".bak").mkdir()
        with self.assertRaises(RuntimeError):
            store.load()
        self.assertEqual(p.read_text(encoding="utf-8"), original)


class TestConcurrency(StoreTestBase):
    def test_mutate_serializes_concurrent_writers(self):
        # two writers add distinct contexts through mutate(); neither may be
        # lost (the failure a last-writer-wins overwrite would cause)
        import threading
        store.load()  # seed the store on disk first
        barrier = threading.Barrier(2)

        def add(name):
            barrier.wait()  # maximise the overlap window
            store.mutate(lambda fresh: fresh["contexts"].append(
                {"name": name, "description": "d", "keywords": [], "axes": [], "traps": []}))

        threads = [threading.Thread(target=add, args=(n,)) for n in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        names = store.list_context_names(store.load())
        self.assertIn("alpha", names)
        self.assertIn("beta", names)  # would be missing under a lost update

    def test_lock_is_exclusive_and_times_out(self):
        from phases_oss.multidim.store import _file_lock
        lock_path = paths.store_path().with_name("store.json.lock")
        with _file_lock(lock_path):
            # a second acquisition while held must not succeed within the timeout
            # (independent fds contend through the native OS lock)
            with self.assertRaises(TimeoutError):
                with _file_lock(lock_path, timeout=0.2):
                    pass
        # released -> can be taken again immediately
        with _file_lock(lock_path, timeout=1.0):
            pass

    def test_lock_released_on_scope_exit_even_after_error(self):
        from phases_oss.multidim.store import _file_lock
        lock_path = paths.store_path().with_name("store.json.lock")
        with self.assertRaises(RuntimeError):
            with _file_lock(lock_path):
                raise RuntimeError("boom")
        # the lock was released despite the error -> re-acquirable
        with _file_lock(lock_path, timeout=1.0):
            pass


class TestNeutrality(unittest.TestCase):
    def test_base_contexts_are_neutral(self):
        base_contexts.assert_neutral()  # must not raise

    def test_guard_trips_on_personal_token(self):
        poisoned = base_contexts.base_contexts()
        # a generic leak indicator (an absolute local path) must trip the guard
        poisoned[0]["description"] += " see notes at c:/users/alice/secret"
        with self.assertRaises(AssertionError):
            base_contexts.assert_neutral(poisoned)

    def test_private_denylist_extends_the_guard(self):
        # a private marker is NOT in the shipped generic list, but an out-of-band
        # PHASES_OSS_EXTRA_FORBIDDEN denylist must still catch it
        poisoned = base_contexts.base_contexts()
        poisoned[0]["description"] += " client acme-secret-codename here"
        base_contexts.assert_neutral(poisoned)  # not caught by the generic list
        prev = os.environ.get("PHASES_OSS_EXTRA_FORBIDDEN")
        os.environ["PHASES_OSS_EXTRA_FORBIDDEN"] = "acme-secret-codename, other"
        try:
            with self.assertRaises(AssertionError):
                base_contexts.assert_neutral(poisoned)
        finally:
            if prev is None:
                os.environ.pop("PHASES_OSS_EXTRA_FORBIDDEN", None)
            else:
                os.environ["PHASES_OSS_EXTRA_FORBIDDEN"] = prev

    def test_base_contexts_deterministic(self):
        self.assertEqual(
            json.dumps(base_contexts.base_contexts(), ensure_ascii=False),
            json.dumps(base_contexts.base_contexts(), ensure_ascii=False),
        )

    def test_no_personal_path_in_serialized_store(self):
        blob = json.dumps(base_contexts.base_contexts(), ensure_ascii=False).lower()
        for tok in ("c:/users", "c:\\users", "/home/", ".multidim", ".mempalace"):
            self.assertNotIn(tok, blob)


if __name__ == "__main__":
    unittest.main()
