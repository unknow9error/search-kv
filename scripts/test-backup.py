#!/usr/bin/env python3
"""Offline backup safety tests; --integration adds a real disposable PostgreSQL 17 drill."""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location("meken_backup", Path(__file__).with_name("meken-backup.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "backups"
        self.environment = patch.dict(os.environ, {"MEKEN_BACKUP_ROOT": str(self.root)}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_production_requires_encryption_and_external_destination(self):
        with self.assertRaisesRegex(module.BackupError, "AGE_RECIPIENT"):
            module.Config()
        os.environ["MEKEN_BACKUP_AGE_RECIPIENT"] = "age1public-key"
        with self.assertRaisesRegex(module.BackupError, "s3://"):
            module.Config()
        os.environ["MEKEN_BACKUP_DESTINATION"] = "s3://meken-backup-bucket/production"
        self.assertEqual(module.Config().bucket, "meken-backup-bucket")

    def test_local_rehearsal_cannot_accidentally_upload(self):
        os.environ["MEKEN_BACKUP_DESTINATION"] = "s3://bucket/backups"
        with self.assertRaisesRegex(module.BackupError, "--local-only"):
            module.Config(local_only=True)

    def test_destination_rejects_path_traversal_credentials_and_primary_host(self):
        for destination in ["s3://bucket/../production", "s3://user:password@bucket/data", "file:///tmp/backups", "s3://bucket"]:
            with self.subTest(destination=destination), self.assertRaises(module.BackupError):
                module.Config.s3_destination(destination)
        os.environ.update(MEKEN_BACKUP_DESTINATION="s3://bucket/data", MEKEN_BACKUP_AGE_RECIPIENT="age1public-key")
        for endpoint in ["http://backup.example", "https://194.238.43.134", "https://u:p@backup.example", "https://backup.example?token=secret"]:
            with self.subTest(endpoint=endpoint):
                os.environ["MEKEN_BACKUP_S3_ENDPOINT"] = endpoint
                with self.assertRaises(module.BackupError):
                    module.Config()

    def test_config_is_not_executed_and_rejects_insecure_files(self):
        target = Path(self.temporary.name) / "executed"
        config = Path(self.temporary.name) / "backup.env"
        config.write_text(f'MEKEN_BACKUP_AGE_RECIPIENT="$(touch {target})"\n')
        config.chmod(0o600)
        module.load_env(config)
        self.assertFalse(target.exists())
        self.assertEqual(os.environ["MEKEN_BACKUP_AGE_RECIPIENT"], f"$(touch {target})")
        config.chmod(0o644)
        with self.assertRaisesRegex(module.BackupError, "0600"):
            module.load_env(config)

    def test_private_directories_reject_symlinks_and_public_permissions(self):
        public = Path(self.temporary.name) / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(module.BackupError):
            module.private_dir(public)
        self.root.symlink_to(public, target_is_directory=True)
        with self.assertRaises(module.BackupError):
            module.private_dir(self.root / "nested")

    def test_second_runner_cannot_enter_locked_backup(self):
        module.private_dir(self.root)
        with (self.root / ".backup.lock").open("a") as lock:
            module.fcntl.flock(lock, module.fcntl.LOCK_EX | module.fcntl.LOCK_NB)
            with patch.object(module.Runner, "source_container") as source:
                with self.assertRaisesRegex(module.BackupError, "already running"):
                    module.backup(module.Config(local_only=True))
                source.assert_not_called()

    def test_full_pending_backlog_stops_before_source_access(self):
        module.private_dir(self.root)
        module.private_dir(self.root / "pending")
        for token in ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"]:
            module.private_dir(self.root / "pending" / ("meken-20261004T000000Z-" + token))
        with patch.object(module.Runner, "source_container") as source:
            with self.assertRaisesRegex(module.BackupError, "backlog"):
                module.backup(module.Config(local_only=True))
            source.assert_not_called()

    def test_inherited_aws_endpoint_is_overridden(self):
        os.environ["AWS_ENDPOINT_URL_S3"] = "http://localhost:9000"
        runner = module.Runner(module.Config(local_only=True))
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as call:
            runner.run(["aws", "--version"])
        self.assertEqual(call.call_args.kwargs["env"]["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"], "true")

    def test_rotation_preserves_retained_pending_recent_and_unuploaded(self):
        config = module.Config(local_only=True)
        created = (module.utcnow() - dt.timedelta(days=40)).isoformat()
        old = "meken-20260101T000000Z-aaaaaaaaaaaa"
        for category in ["daily", "pending", "retained"]:
            module.private_dir(self.root / category)
            path = self.root / category / old
            module.private_dir(path)
            module.write_json(path / "COMPLETE.json", {"created_at": created, "artifact": old, "retained": category == "retained"})
            module.write_json(path / "UPLOADED.json", {"verified": True})
        unuploaded = self.root / "daily" / "meken-20260101T000000Z-bbbbbbbbbbbb"
        module.private_dir(unuploaded)
        module.write_json(unuploaded / "COMPLETE.json", {"created_at": created, "artifact": unuploaded.name, "retained": False})
        recent = self.root / "daily" / "meken-20261004T000000Z-cccccccccccc"
        module.private_dir(recent)
        module.write_json(recent / "COMPLETE.json", {"created_at": module.utcnow().isoformat(), "artifact": recent.name, "retained": False})
        module.write_json(recent / "UPLOADED.json", {"verified": True})
        module.rotate_local(config)
        self.assertFalse((self.root / "daily" / old).exists())
        for path in [self.root / "pending" / old, self.root / "retained" / old, unuploaded, recent]:
            self.assertTrue(path.exists())

    def test_versioned_bucket_is_rejected_before_dump(self):
        os.environ.update(MEKEN_BACKUP_DESTINATION="s3://bucket/data", MEKEN_BACKUP_AGE_RECIPIENT="age1public-key")
        runner = module.Runner(module.Config())
        with patch.object(runner, "run", side_effect=[b"aws-cli/2.37.9", b'{"Status":"Enabled"}']):
            with self.assertRaisesRegex(module.BackupError, "unversioned"):
                module.S3Store(runner)

    def test_external_checksum_failure_does_not_create_uploaded_receipt(self):
        os.environ.update(MEKEN_BACKUP_DESTINATION="s3://bucket/data", MEKEN_BACKUP_AGE_RECIPIENT="age1public-key")
        config = module.Config()
        module.private_dir(self.root)
        work = self.root / "meken-20261004T000000Z-aaaaaaaaaaaa"
        module.private_dir(work)
        for name in ["backup.tar.age", "manifest.json", "SHA256SUMS"]:
            (work / name).write_bytes(b"original")
        runner = module.Runner(config)

        def mocked_transfer(command, **kwargs):
            if command[-1] == "--version":
                return b"aws-cli/2.37.9"
            if "get-bucket-versioning" in command:
                return b"{}"
            if "cp" in command and command[3].startswith("s3://"):
                Path(command[4]).write_bytes(b"corrupt")
            return b""

        with patch.object(runner, "run", side_effect=mocked_transfer):
            store = module.S3Store(runner)
            with self.assertRaisesRegex(module.BackupError, "checksum"):
                store.upload(work, "daily", {"format": 1})
        self.assertFalse((work / "UPLOADED.json").exists())


def integration(require_age=False) -> None:
    """No production access: all DB state lives in a unique network-none PG17 container."""
    token = uuid.uuid4().hex
    source = "meken-backup-test-" + token
    volume = source
    with tempfile.TemporaryDirectory(prefix="meken-backup-rehearsal-") as temporary:
        root = Path(temporary).resolve() / "backups"
        with patch.dict(os.environ, {"MEKEN_BACKUP_ROOT": str(root), "MEKEN_BACKUP_SOURCE_CONTAINER": source,
                                     "MEKEN_BACKUP_DB_USER": "meken_admin", "MEKEN_BACKUP_DATABASE": "meken"}, clear=False):
            os.environ.pop("MEKEN_BACKUP_DESTINATION", None)
            config = module.Config(local_only=True)
            runner = module.Runner(config)
            try:
                runner.run(["docker", "volume", "create", "--label", "kz.unknown.meken.backup-test=" + token, volume])
                runner.run(["docker", "run", "--detach", "--pull=never", "--name", source, "--network=none", "--memory=768m",
                            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data", "--env", "POSTGRES_USER=meken_admin",
                            "--env", "POSTGRES_DB=meken", "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm"])
                ready = False
                for _ in range(60):
                    try:
                        runner.run(["docker", "exec", source, "psql", "-h", "127.0.0.1", "-U", "meken_admin", "-d", "meken", "-c", "SELECT 1;"], timeout=5)
                        ready = True
                        break
                    except module.BackupError:
                        module.time.sleep(1)
                if not ready:
                    raise module.BackupError("Test source did not initialize")
                runner.run(runner.psql(source), input=b"""
                    CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
                    INSERT INTO alembic_version VALUES ('backup-rehearsal-only');
                    CREATE TABLE users (id int PRIMARY KEY, name text NOT NULL);
                    CREATE TABLE conversations (id int PRIMARY KEY, user_id int NOT NULL REFERENCES users(id));
                    CREATE INDEX conversations_user_idx ON conversations(user_id);
                    INSERT INTO users VALUES (1, 'isolated rehearsal'), (2, 'not production');
                    INSERT INTO conversations VALUES (1,1),(2,1),(3,2);
                    CREATE TABLE "strange""table" (price bigint NOT NULL DEFAULT 10000000000);
                    INSERT INTO "strange""table" VALUES (10000000000);
                """)
                module.private_dir(root)
                work = root / "direct-drill"
                module.private_dir(work)
                original_run = runner.run
                wrote = False

                def concurrent_write(command, **kwargs):
                    nonlocal wrote
                    if "pg_dump" in command and not wrote:
                        # Write AFTER exporting/counting snapshot, BEFORE pg_dump imports it.
                        original_run(runner.psql(source), input=b"INSERT INTO users VALUES (3,'concurrent writer');\n")
                        wrote = True
                    return original_run(command, **kwargs)

                with patch.object(runner, "run", side_effect=concurrent_write):
                    dump, baseline, image = module.create_dump(runner, source, work)
                if next(table["rows"] for table in baseline["tables"] if table["name"] == "users") != 2:
                    raise AssertionError("Baseline snapshot changed")
                restored = module.restore_test(runner, dump, baseline, image)
                if original_run(runner.psql(source), input=b"SELECT count(*) FROM users;\n").strip() != b"3":
                    raise AssertionError("Concurrent source write was not preserved")
                altered = json.loads(json.dumps(baseline))
                altered["tables"][0]["rows"] += 1
                try:
                    module.restore_test(runner, dump, altered, image)
                except module.BackupError as exc:
                    if "inventory differs" not in str(exc):
                        raise
                else:
                    raise AssertionError("Changed inventory was incorrectly accepted")
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    artifact = module.backup(config)
                if "LOCAL REHEARSAL ONLY" not in output.getvalue() or (artifact / "UPLOADED.json").exists():
                    raise AssertionError("Local rehearsal pretended to be external backup success")
                if artifact.stat().st_mode & 0o077 or any(path.stat().st_mode & 0o077 for path in artifact.iterdir()):
                    raise AssertionError("Backup file permissions are public")
                if shutil.which("age") and shutil.which("age-keygen"):
                    identity = Path(temporary) / "identity.txt"
                    original_run(["age-keygen", "--output", str(identity)])
                    identity.chmod(0o600)
                    recipient = original_run(["age-keygen", "-y", str(identity)]).decode().strip()
                    encrypted = Path(temporary) / "backup.tar.age"
                    original_run(["age", "--recipient", recipient, "--output", str(encrypted), str(artifact / "backup.tar")])
                    with contextlib.redirect_stdout(io.StringIO()) as recovery_output:
                        module.verify_archive(config, encrypted, identity, None)
                    if "ARCHIVE RECOVERY VERIFIED" not in recovery_output.getvalue():
                        raise AssertionError("Encrypted recovery was not verified")
                    corrupted = Path(temporary) / "corrupt.tar.age"
                    cipher = bytearray(encrypted.read_bytes())
                    cipher[-1] ^= 1
                    corrupted.write_bytes(cipher)
                    try:
                        module.verify_archive(config, corrupted, identity, None)
                    except module.BackupError:
                        pass
                    else:
                        raise AssertionError("Corrupt ciphertext was accepted")
                    print("REAL AGE RECOVERY PASSED: temporary offsite key decrypts archive, PG17 inventory matches, corrupt ciphertext rejected")
                elif require_age:
                    raise AssertionError("--require-age needs real age and age-keygen in PATH")
                print(f"REAL PG17 RESTORE DRILL PASSED: {restored['tables']} tables/{restored['rows']} rows; "
                      "snapshot survives concurrent source write; schema/FK/index/migration comparison and mismatch rejection verified")
            finally:
                with contextlib.suppress(module.BackupError):
                    runner.run(["docker", "rm", "--force", source], timeout=30)
                runner.run(["docker", "volume", "rm", volume], timeout=30)


if __name__ == "__main__":
    run_integration = "--integration" in sys.argv
    require_age = "--require-age" in sys.argv
    if run_integration:
        sys.argv.remove("--integration")
    if require_age:
        sys.argv.remove("--require-age")
    result = unittest.main(exit=False).result
    if not result.wasSuccessful():
        sys.exit(1)
    if run_integration:
        integration(require_age=require_age)
