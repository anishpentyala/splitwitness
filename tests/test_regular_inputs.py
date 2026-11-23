"""Regular manifest admission, without waiting for FIFO writers."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, Limits, audit, audit_memberships
from splitwitness.core import _rows

HAS_FIFO = hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK")


class RegularInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = self.root / "records.csv"
        self.links = self.root / "links.csv"
        self.memberships = self.root / "memberships.csv"
        self.records.write_text("id,split\na,train\nb,test\n")
        self.links.write_text("left_id,right_id,relation\na,b,r\n")
        self.memberships.write_text("record_id,namespace,group_id\na,g,x\nb,g,x\n")

    @unittest.skipUnless(HAS_FIFO, "requires nonblocking POSIX FIFO")
    def test_cli_refuses_writerless_pipes_in_all_input_roles(self):
        pipe = self.root / "pipe.csv"
        os.mkfifo(pipe)
        for mode in ("links", "memberships"):
            for role in ("records", "constraints"):
                with self.subTest(mode=mode, role=role):
                    records = pipe if role == "records" else self.records
                    constraints = (
                        pipe
                        if role == "constraints"
                        else (self.links if mode == "links" else self.memberships)
                    )
                    command = [
                        sys.executable,
                        "-m",
                        "splitwitness",
                        str(records),
                        str(constraints),
                    ]
                    if mode == "memberships":
                        command.append("--memberships")
                    result = subprocess.run(
                        command, capture_output=True, text=True, timeout=3
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("regular file", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(HAS_FIFO, "requires nonblocking POSIX FIFO")
    def test_api_refuses_pipe_and_pipe_symlink_with_deadline(self):
        pipe = self.root / "pipe.csv"
        os.mkfifo(pipe)
        paths = [pipe]
        if hasattr(os, "symlink"):
            alias = self.root / "alias.csv"
            try:
                alias.symlink_to(pipe)
                paths.append(alias)
            except OSError:
                pass
        for path in paths:
            command = [
                sys.executable,
                "-c",
                "import sys; from splitwitness import audit,AuditError; "
                "\ntry: audit(sys.argv[1],sys.argv[2])"
                "\nexcept AuditError as e: print(str(e)); sys.exit(2)",
                str(path),
                str(self.links),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertIn("regular file", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_descriptor_not_a_prior_path_stat_decides_admission(self):
        import stat
        from types import SimpleNamespace

        with (
            patch(
                "splitwitness.core.os.fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFIFO),
            ),
            self.assertRaisesRegex(AuditError, "regular file"),
        ):
            audit(self.records, self.links)

    def test_opener_adds_nonblocking_flag_and_preserves_regular_bytes(self):
        original = os.open
        with patch("splitwitness.core.os.open", wraps=original) as opened:
            report = audit(self.records, self.links)
        self.assertEqual(opened.call_count, 2)
        for args, _ in opened.call_args_list:
            if hasattr(os, "O_NONBLOCK"):
                self.assertEqual(args[1] & os.O_NONBLOCK, os.O_NONBLOCK)
        self.assertFalse(report["passed"])

    def test_read_limit_is_exactly_cap_plus_one(self):
        import io
        import stat
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        stream = MagicMock(wraps=io.BytesIO(b"id,split\na,x\n"))
        stream.__enter__.return_value = stream
        stream.fileno.return_value = 0
        stream.read.return_value = b"id,sp"
        with (
            patch("splitwitness.core.open", return_value=stream),
            patch(
                "splitwitness.core.os.fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG),
            ),
            self.assertRaisesRegex(AuditError, "byte limit"),
        ):
            _rows(self.records, ["id", "split"], "records", Limits(max_bytes=4), 10)
        stream.read.assert_called_once_with(5)
        stream.__exit__.assert_called_once()

    @unittest.skipUnless(Path("/dev/null").exists(), "requires Unix null device")
    def test_device_is_rejected_by_library(self):
        for run, constraints in (
            (audit, self.links),
            (audit_memberships, self.memberships),
        ):
            with self.assertRaisesRegex(AuditError, "regular file"):
                run("/dev/null", constraints)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_symlinks_to_regular_manifests_preserve_reports_and_receipts(self):
        aliases = []
        for path in (self.records, self.links, self.memberships):
            alias = path.with_suffix(".alias")
            try:
                alias.symlink_to(path)
            except OSError:
                self.skipTest("symlink creation unavailable")
            aliases.append(alias)
        self.assertEqual(
            audit(self.records, self.links, examples=1), audit(*aliases[:2], examples=1)
        )
        self.assertEqual(
            audit_memberships(self.records, self.memberships, examples=1),
            audit_memberships(aliases[0], aliases[2], examples=1),
        )

    def test_admitted_regular_input_read_remains_bounded(self):
        with patch("splitwitness.core.open", wraps=open) as opened:
            with self.assertRaisesRegex(AuditError, "byte limit"):
                _rows(self.records, ["id", "split"], "records", Limits(max_bytes=4), 10)
        opened.assert_called_once()

    @unittest.skipUnless(
        Path("/proc/self/fd").is_dir() and HAS_FIFO, "requires Linux descriptors"
    )
    def test_repeated_refusals_do_not_leak_descriptors(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(100):
            with self.assertRaisesRegex(AuditError, "regular file"):
                audit("/dev/null", self.links)
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)
