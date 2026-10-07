#!/usr/bin/env python3
"""Consistent, restore-tested PostgreSQL backups. Requires Python 3.10+, Docker.

Production success requires age encryption and verified S3 upload. --local-only
is a deliberately separate rehearsal mode. No restore targets the source DB.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
import uuid


class BackupError(RuntimeError):
    pass


ARTIFACT_ID = re.compile(r"meken-\d{8}T\d{6}Z-[a-f0-9]{12}\Z")
PAYLOAD_FILES = {"backup.tar.age", "manifest.json", "SHA256SUMS", "COMPLETE.json"}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _digest(stream)


def _digest(stream) -> str:
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_dir(path: Path) -> None:
    # Reject symlink components: backup paths must not redirect into public dirs.
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise BackupError("Backup directory contains a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise BackupError("Backup directories must belong to the runner and have mode 0700")


def load_env(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise BackupError("Backup config must be a regular private file (0600)")
    if path.stat().st_uid not in {os.getuid(), 0}:
        raise BackupError("Backup config must belong to the runner or root")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise BackupError("Invalid config assignment")
        name, raw = stripped.split("=", 1)
        if not re.fullmatch(r"(?:MEKEN_BACKUP_[A-Z0-9_]+|AWS_[A-Z0-9_]+)", name):
            raise BackupError("Unsupported backup config variable")
        tokens = shlex.split(raw, comments=True)
        if len(tokens) > 1:
            raise BackupError("Quote config values containing spaces")
        os.environ[name] = tokens[0] if tokens else ""


class Config:
    def __init__(self, local_only: bool = False, *, offline_verify=False):
        self.local_only = local_only
        self.root = Path(os.environ.get("MEKEN_BACKUP_ROOT", "/opt/meken/backups"))
        self.project = Path(os.environ.get("MEKEN_BACKUP_PROJECT", "/opt/meken/current"))
        self.compose_env = Path(os.environ.get("MEKEN_BACKUP_COMPOSE_ENV", "/opt/meken/shared/.env"))
        self.source = os.environ.get("MEKEN_BACKUP_SOURCE_CONTAINER", "")
        self.database = os.environ.get("MEKEN_BACKUP_DATABASE", "meken")
        self.user = os.environ.get("MEKEN_BACKUP_DB_USER", "meken_admin")
        self.destination = os.environ.get("MEKEN_BACKUP_DESTINATION", "")
        self.recipient = os.environ.get("MEKEN_BACKUP_AGE_RECIPIENT", "")
        self.endpoint = os.environ.get("MEKEN_BACKUP_S3_ENDPOINT", "")
        self.retain_days = self.integer("RETAIN_DAYS", 30, 1, 90)
        self.timeout = self.integer("TIMEOUT_SECONDS", 900, 30, 7200)
        self.min_free_mb = self.integer("MIN_FREE_MB", 1024, 128, 1048576)
        self.max_pending = self.integer("MAX_PENDING", 3, 1, 100)
        self.restore_memory = os.environ.get("MEKEN_BACKUP_RESTORE_MEMORY", "768m")
        if not re.fullmatch(r"[1-9][0-9]*[mg]", self.restore_memory):
            raise BackupError("Restore memory must use an integer m/g limit")
        if not self.root.is_absolute() or not self.project.is_absolute():
            raise BackupError("Backup root and project must be absolute paths")
        for name in [self.database, self.user]:
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", name):
                raise BackupError("Invalid PostgreSQL database or role name")
        if not local_only and not offline_verify:
            if not self.recipient:
                raise BackupError("MEKEN_BACKUP_AGE_RECIPIENT is required for production backups")
            self.bucket, self.prefix = self.s3_destination(self.destination)
            if self.endpoint:
                endpoint = urlsplit(self.endpoint)
                if endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in {"", "/"}:
                    raise BackupError("S3 endpoint must be an HTTPS origin without credentials")
                if not endpoint.hostname or endpoint.hostname in {"localhost", "127.0.0.1", "::1", "194.238.43.134"}:
                    raise BackupError("S3 destination must be outside the production server")
                try:
                    addresses = {entry[4][0] for entry in socket.getaddrinfo(endpoint.hostname, endpoint.port or 443)}
                    local_addresses = {entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None)}
                except OSError as exc:
                    raise BackupError("Cannot resolve the offsite S3 endpoint") from exc
                if addresses & (local_addresses | {"127.0.0.1", "::1", "194.238.43.134"}) or any(address.startswith("127.") for address in addresses):
                    raise BackupError("S3 endpoint resolves to the production or local host")
        elif local_only and self.destination:
            raise BackupError("--local-only cannot be combined with an external destination")

    @staticmethod
    def integer(name: str, default: int, lower: int, upper: int) -> int:
        try:
            value = int(os.environ.get("MEKEN_BACKUP_" + name, str(default)))
        except ValueError as exc:
            raise BackupError("Invalid integer backup setting") from exc
        if not lower <= value <= upper:
            raise BackupError("Backup integer setting is outside its supported bounds")
        return value

    @staticmethod
    def s3_destination(value: str) -> tuple[str, str]:
        parsed = urlsplit(value)
        if parsed.scheme != "s3" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BackupError("MEKEN_BACKUP_DESTINATION must be an explicit s3://bucket/prefix")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", parsed.netloc):
            raise BackupError("Invalid S3 bucket")
        prefix = parsed.path.strip("/")
        if not prefix or not re.fullmatch(r"[a-zA-Z0-9_./-]+", prefix) or any(p in {".", "..", ""} for p in prefix.split("/")):
            raise BackupError("Use a dedicated non-empty S3 backup prefix")
        return parsed.netloc, prefix


class Runner:
    def __init__(self, config: Config):
        self.config = config

    def run(self, command: list[str], *, stdin=None, stdout=None, input=None, timeout=None) -> bytes:
        # Never print command arguments, credentials, dump contents, or SQL errors.
        try:
            result = subprocess.run(command, stdin=stdin, stdout=stdout or subprocess.PIPE,
                                    stderr=subprocess.PIPE, input=input,
                                    timeout=timeout or self.config.timeout, check=False,
                                    env={**os.environ, "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true", "AWS_PAGER": ""}
                                    if Path(command[0]).name == "aws" else None)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError(f"{Path(command[0]).name} failed or timed out") from exc
        if result.returncode:
            raise BackupError(f"{Path(command[0]).name} exited with status {result.returncode}")
        return result.stdout or b""

    def source_container(self) -> str:
        if self.config.source:
            container = self.config.source
        else:
            container = self.run(["docker", "compose", "--project-directory", str(self.config.project),
                                  "--env-file", str(self.config.compose_env), "-f", str(self.config.project / "compose.yml"),
                                  "-f", str(self.config.project / "infra/compose.production.yml"), "ps", "-q", "db"]).decode().strip()
        if not container or "\n" in container:
            raise BackupError("Expected exactly one running source PostgreSQL container")
        return container

    def psql(self, container: str, *, restore=False) -> list[str]:
        return ["docker", "exec", "-i", container, "psql", "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
                "--set=ON_ERROR_STOP=1", "--username", "meken_restore" if restore else self.config.user,
                "--dbname", "meken_restore" if restore else self.config.database] + (["--host=127.0.0.1"] if restore else [])


class SQLSession:
    """Keep the exported snapshot open, with bounded, framed psql responses."""
    def __init__(self, command: list[str], timeout: int):
        self.timeout = timeout
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.errors, bufsize=0)
        self.buffer = b""
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def query(self, sql: str) -> list[str]:
        marker = uuid.uuid4().hex
        try:
            self.process.stdin.write((sql + f"\nSELECT '{marker}';\n").encode())
            self.process.stdin.flush()
        except BrokenPipeError as exc:
            raise BackupError("Snapshot session failed") from exc
        end = time.monotonic() + self.timeout
        lines = []
        while True:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                text = line.decode().rstrip("\r")
                if text == marker:
                    return lines
                if text:
                    lines.append(text)
                continue
            remaining = end - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise BackupError("Snapshot SQL timed out")
            block = os.read(self.process.stdout.fileno(), 65536)
            if not block:
                raise BackupError("Snapshot SQL failed (details withheld to protect data)")
            self.buffer += block

    def close(self):
        self.selector.close()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.errors.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inventory(session: SQLSession) -> dict:
    # No user content, tokens, prices, or chat text enter the inventory.
    scope = "n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'"
    tables = [json.loads(line) for line in session.query(
        "SELECT json_build_object('schema', n.nspname, 'name', c.relname, 'kind', c.relkind) "
        f"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE {scope} "
        "AND c.relkind IN ('r','p','m') ORDER BY n.nspname,c.relname;"
    )]
    if not any(table["schema"] == "public" and table["name"] == "alembic_version" for table in tables):
        raise BackupError("Source is missing Alembic migration state")
    for table in tables:
        relation = identifier(table["schema"]) + "." + identifier(table["name"])
        table["rows"] = int(session.query(f"SELECT count(*) FROM {relation};")[0])
    migrations = session.query("SELECT version_num FROM public.alembic_version ORDER BY version_num;")
    if not migrations:
        raise BackupError("Alembic migration state is empty")
    columns = [json.loads(line) for line in session.query(
        "SELECT json_build_object('schema',n.nspname,'table',c.relname,'name',a.attname,"
        "'type',format_type(a.atttypid,a.atttypmod),'nullable',NOT a.attnotnull,"
        "'default',pg_get_expr(d.adbin,d.adrelid)) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        f"WHERE {scope} AND c.relkind IN ('r','p','m') AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY n.nspname,c.relname,a.attnum;"
    )]
    constraints = [json.loads(line) for line in session.query(
        "SELECT json_build_object('schema',n.nspname,'table',c.relname,'name',con.conname,"
        "'definition',pg_get_constraintdef(con.oid),'validated',con.convalidated) "
        "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE {scope} ORDER BY n.nspname,c.relname,con.conname;"
    )]
    indexes = [json.loads(line) for line in session.query(
        "SELECT json_build_object('schema',n.nspname,'table',c.relname,'definition',pg_get_indexdef(i.indexrelid)) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE {scope} ORDER BY n.nspname,c.relname,pg_get_indexdef(i.indexrelid);"
    )]
    return {"tables": tables, "columns": columns, "constraints": constraints, "indexes": indexes, "migrations": migrations}


def create_dump(runner: Runner, source: str, directory: Path) -> tuple[Path, dict, str]:
    image = runner.run(["docker", "inspect", "--format", "{{.Image}}", source]).decode().strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise BackupError("Could not resolve source PostgreSQL image ID")
    dump = directory / "database.dump"
    with SQLSession(runner.psql(source), runner.config.timeout) as session:
        size = int(session.query(f"SELECT pg_database_size('{runner.config.database}');")[0])
        if shutil.disk_usage(directory).free < max(runner.config.min_free_mb * 1024 * 1024, size * 3):
            raise BackupError("Insufficient free disk for dump, encryption, and external read-back")
        session.query(f"SET statement_timeout = {runner.config.timeout * 1000}; "
                      f"SET idle_in_transaction_session_timeout = {(runner.config.timeout + 60) * 1000}; "
                      "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
        snapshot = session.query("SELECT pg_export_snapshot();")[0]
        if not re.fullmatch(r"[A-Fa-f0-9-]+", snapshot):
            raise BackupError("Unexpected PostgreSQL snapshot identifier")
        baseline = inventory(session)
        with dump.open("xb") as output:
            runner.run(["docker", "exec", source, "pg_dump", "--username", runner.config.user,
                        "--dbname", runner.config.database, "--format=custom", "--compress=gzip:6",
                        "--lock-wait-timeout=30s", "--snapshot", snapshot], stdout=output)
            output.flush()
            os.fsync(output.fileno())
        session.query("COMMIT;")
    if dump.stat().st_size < 32:
        raise BackupError("PostgreSQL dump is empty")
    return dump, baseline, image


def restore_test(runner: Runner, dump: Path, baseline: dict, image: str) -> dict:
    # Image ID of source, no network, no published ports, no source mounts/volume.
    token = uuid.uuid4().hex
    name = "meken-restore-" + token
    volume = "meken-restore-" + token
    created = False
    try:
        runner.run(["docker", "volume", "create", "--label", "kz.unknown.meken.restore=" + token, volume])
        created = True
        runner.run(["docker", "run", "--detach", "--pull=never", "--name", name,
                    "--label", "kz.unknown.meken.restore=" + token, "--network=none", "--memory", runner.config.restore_memory,
                    "--cpus=1", "--security-opt=no-new-privileges:true", "--mount",
                    f"type=volume,source={volume},target=/var/lib/postgresql/data", "--tmpfs", "/tmp:rw,nosuid,noexec",
                    "--env", "POSTGRES_DB=meken_restore", "--env", "POSTGRES_USER=meken_restore",
                    "--env", "POSTGRES_HOST_AUTH_METHOD=trust", image])
        ready = False
        for _ in range(60):
            try:
                # pg_isready also succeeds during entrypoint's temporary init server.
                # Wait for init completion before testing the permanent server.
                logs = runner.run(["docker", "logs", name], timeout=5)
                if b"PostgreSQL init process complete; ready for start up." not in logs:
                    time.sleep(1)
                    continue
                runner.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "meken_restore", "-d", "meken_restore"], timeout=5)
                runner.run(runner.psql(name, restore=True), input=b"SELECT 1;\n", timeout=5)
                ready = True
                break
            except BackupError:
                time.sleep(1)
        if not ready:
            raise BackupError("Isolated restore PostgreSQL did not become ready")
        with dump.open("rb") as input_stream:
            runner.run(["docker", "exec", "-i", name, "pg_restore", "--username", "meken_restore",
                        "--host=127.0.0.1", "--dbname", "meken_restore", "--no-owner", "--no-acl", "--exit-on-error",
                        "--single-transaction"], stdin=input_stream)
        with SQLSession(runner.psql(name, restore=True), runner.config.timeout) as session:
            session.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
            restored = inventory(session)
            session.query("COMMIT;")
        if baseline != restored:
            raise BackupError("Restore inventory differs: tables/rows/columns/constraints/indexes/migrations")
        return {"verified_at": utcnow().isoformat(), "image": image, "tables": len(baseline["tables"]),
                "rows": sum(table["rows"] for table in baseline["tables"]), "migrations": baseline["migrations"]}
    finally:
        # Names are generated here; never accept a restore target from the caller.
        cleanup_failed = False
        try:
            runner.run(["docker", "rm", "--force", name], timeout=30)
        except BackupError:
            if created:
                cleanup_failed = True
        if created:
            try:
                runner.run(["docker", "volume", "rm", volume], timeout=30)
            except BackupError:
                cleanup_failed = True
        if cleanup_failed:
            raise BackupError("Isolated restore cleanup failed; inspect labelled meken-restore-* resources")


class S3Store:
    def __init__(self, runner: Runner):
        self.runner = runner
        self.config = runner.config
        self.base = ["aws"] + (["--endpoint-url", self.config.endpoint] if self.config.endpoint else [])
        version = runner.run(["aws", "--version"]).decode()
        if not version.startswith("aws-cli/2."):
            raise BackupError("AWS CLI v2 is required")
        versioning = json.loads(runner.run(self.base + ["s3api", "get-bucket-versioning", "--bucket", self.config.bucket, "--output", "json"]))
        if versioning.get("Status") in {"Enabled", "Suspended"}:
            # DeleteObject on versioned buckets leaves user data in noncurrent versions.
            # This runner deliberately supports unversioned dedicated backup buckets.
            raise BackupError("Use an unversioned dedicated backup bucket; versioned retention is unsupported")

    def uri(self, key: str) -> str:
        return f"s3://{self.config.bucket}/{key}"

    def upload(self, directory: Path, category: str, marker: dict) -> None:
        prefix = f"{self.config.prefix}/{category}/{directory.name}/"
        write_json(directory / "COMPLETE.json", marker)
        for name in ["backup.tar.age", "manifest.json", "SHA256SUMS", "COMPLETE.json"]:
            source = directory / name
            self.runner.run(self.base + ["s3", "cp", str(source), self.uri(prefix + name),
                                         "--only-show-errors", "--no-progress", "--checksum-algorithm", "SHA256"])
            with tempfile.TemporaryDirectory(prefix=".verify-", dir=self.config.root) as temporary:
                downloaded = Path(temporary) / name
                self.runner.run(self.base + ["s3", "cp", self.uri(prefix + name), str(downloaded),
                                             "--only-show-errors", "--no-progress", "--checksum-mode", "ENABLED"])
                if digest(source) != digest(downloaded):
                    raise BackupError("External backup read-back checksum differs")

    def rotate(self) -> None:
        prefix = f"{self.config.prefix}/daily/"
        output = self.runner.run(self.base + ["s3api", "list-objects-v2", "--bucket", self.config.bucket,
                                             "--prefix", prefix, "--query", "Contents[].Key", "--output", "json"])
        keys = json.loads(output) or []
        groups: dict[str, set[str]] = {}
        for key in keys:
            if not isinstance(key, str) or not key.startswith(prefix):
                raise BackupError("Unexpected S3 listing")
            parts = key[len(prefix):].split("/")
            if len(parts) == 2 and ARTIFACT_ID.fullmatch(parts[0]) and parts[1] in PAYLOAD_FILES:
                groups.setdefault(parts[0], set()).add(parts[1])
        cutoff = utcnow() - dt.timedelta(days=self.config.retain_days)
        for artifact, names in groups.items():
            # Only complete backups created by this program; retained/other objects excluded.
            if names != PAYLOAD_FILES:
                continue
            with tempfile.TemporaryDirectory(prefix=".rotation-", dir=self.config.root) as temporary:
                marker_path = Path(temporary) / "COMPLETE.json"
                self.runner.run(self.base + ["s3", "cp", self.uri(prefix + artifact + "/COMPLETE.json"),
                                             str(marker_path), "--only-show-errors", "--no-progress", "--checksum-mode", "ENABLED"])
                marker = json.loads(marker_path.read_text())
            if marker.get("artifact") != artifact or marker.get("retained") is not False or marker.get("format") != 1:
                continue
            created = dt.datetime.fromisoformat(marker["created_at"])
            if created.tzinfo is None or created >= cutoff:
                continue
            # Remove marker first: an interrupted deletion cannot advertise a restorable backup.
            for filename in ["COMPLETE.json", "backup.tar.age", "manifest.json", "SHA256SUMS"]:
                self.runner.run(self.base + ["s3api", "delete-object", "--bucket", self.config.bucket,
                                             "--key", prefix + artifact + "/" + filename])


def rotate_local(config: Config) -> None:
    cutoff = utcnow() - dt.timedelta(days=config.retain_days)
    daily = config.root / "daily"
    for directory in daily.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not ARTIFACT_ID.fullmatch(directory.name):
            continue
        marker_path = directory / "COMPLETE.json"
        if marker_path.is_symlink() or not marker_path.is_file() or not (directory / "UPLOADED.json").is_file():
            continue
        marker = json.loads(marker_path.read_text())
        created = dt.datetime.fromisoformat(marker["created_at"])
        if marker.get("artifact") == directory.name and marker.get("retained") is False and created.tzinfo and created < cutoff:
            shutil.rmtree(directory)


def verify_archive(config: Config, encrypted: Path, identity: Path, image: str | None) -> None:
    """Decrypt/recover on a separate computer without touching any source database."""
    os.umask(0o077)
    if identity.is_symlink() or not identity.is_file() or identity.stat().st_mode & 0o077:
        raise BackupError("The age private key must be a private regular file (0600)")
    private_dir(config.root)
    runner = Runner(config)
    with tempfile.TemporaryDirectory(prefix=".recovery-", dir=config.root) as temporary:
        work = Path(temporary)
        tar_path = work / "backup.tar"
        runner.run(["age", "--decrypt", "--identity", str(identity), "--output", str(tar_path), str(encrypted)])
        with tarfile.open(tar_path, mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != 2 or {member.name for member in members} != {"database.dump", "inventory.json"} or not all(member.isfile() for member in members):
                raise BackupError("Invalid backup archive members")
            # No extractall/path traversal; copy only the two known regular files.
            for member in members:
                with archive.extractfile(member) as source, (work / member.name).open("xb") as target:
                    shutil.copyfileobj(source, target)
        stored = json.loads((work / "inventory.json").read_text())
        dump = work / "database.dump"
        if stored.get("format") != 1 or digest(dump) != stored.get("dump_sha256"):
            raise BackupError("Recovered dump checksum differs")
        source_image = image or stored["restore"]["image"]
        # Accept a local image, never CLI flags; restoration always generates target names.
        if source_image.startswith("-") or "\n" in source_image:
            raise BackupError("Invalid restore image")
        image_id = runner.run(["docker", "image", "inspect", "--format", "{{.Id}}", source_image]).decode().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise BackupError("Restore image is not available locally")
        result = restore_test(runner, dump, stored["inventory"], image_id)
        print(f"ARCHIVE RECOVERY VERIFIED: decrypted and restored {result['tables']} tables; source database untouched")


def backup(config: Config, *, keep=False) -> Path:
    os.umask(0o077)
    private_dir(config.root)
    for category in ["daily", "retained", "pending"]:
        private_dir(config.root / category)
    lock_path = config.root / ".backup.lock"
    if lock_path.is_symlink():
        raise BackupError("Backup lock must not be a symlink")
    with lock_path.open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another backup is already running") from exc
        pending = [path for path in (config.root / "pending").iterdir() if ARTIFACT_ID.fullmatch(path.name)]
        if len(pending) >= config.max_pending:
            raise BackupError("Pending backup backlog is full; verify/recover existing copies before an explicit cleanup")
        if shutil.disk_usage(config.root).free < config.min_free_mb * 1024 * 1024:
            raise BackupError("Insufficient free backup disk; no new dump started")
        runner = Runner(config)
        # Fail for missing transfer tools before touching the source database.
        store = None if config.local_only else S3Store(runner)
        if not config.local_only:
            runner.run(["age", "--version"])
            # Parse/check the public recipient before creating a production dump.
            with tempfile.TemporaryDirectory(prefix=".recipient-check-", dir=config.root) as temporary:
                empty = Path(temporary) / "empty"
                empty.write_bytes(b"")
                runner.run(["age", "--recipient", config.recipient, "--output", str(Path(temporary) / "empty.age"), str(empty)])
        artifact = utcnow().strftime("meken-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        work = config.root / "pending" / artifact
        private_dir(work)
        created = utcnow().isoformat()
        source = runner.source_container()
        dump, baseline, image = create_dump(runner, source, work)
        verified = restore_test(runner, dump, baseline, image)
        write_json(work / "inventory.json", {"format": 1, "database": config.database, "created_at": created,
                                            "dump_sha256": digest(dump), "inventory": baseline, "restore": verified})
        # Keep raw private files on failure for diagnosis/retry; never rotate pending.
        tar_path = work / "backup.tar"
        with tarfile.open(tar_path, mode="x") as archive:
            for filename in ["database.dump", "inventory.json"]:
                archive.add(work / filename, arcname=filename, recursive=False)
        payload = tar_path
        if not config.local_only:
            payload = work / "backup.tar.age"
            runner.run(["age", "--recipient", config.recipient, "--output", str(payload), str(tar_path)])
        with payload.open("rb") as completed_payload:
            os.fsync(completed_payload.fileno())
        manifest = {"format": 1, "artifact": artifact, "created_at": created, "retained": keep,
                    "encrypted": not config.local_only, "restore_verified": True,
                    "payload": payload.name, "payload_sha256": digest(payload), "payload_bytes": payload.stat().st_size}
        write_json(work / "manifest.json", manifest)
        checksums = work / "SHA256SUMS"
        with checksums.open("x") as stream:
            for filename in [payload.name, "manifest.json"]:
                stream.write(digest(work / filename) + "  " + filename + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if config.local_only:
            print(f"LOCAL REHEARSAL ONLY: restored {verified['tables']} tables; artifact {work}")
            return work
        # Remove plaintext only after encrypted archive exists; plaintext is never sent externally.
        for filename in ["database.dump", "inventory.json", "backup.tar"]:
            (work / filename).unlink()
        category = "retained" if keep else "daily"
        store.upload(work, category, manifest)
        write_json(work / "UPLOADED.json", {"verified_at": utcnow().isoformat(), "destination": config.destination,
                                           "category": category, "artifact": artifact})
        final = config.root / category / artifact
        sync_directory(work)
        work.rename(final)
        sync_directory(config.root / category)
        sync_directory(config.root / "pending")
        # Rotate only after a fresh encrypted, restore-tested copy has been uploaded and read back.
        store.rotate()
        rotate_local(config)
        print(f"BACKUP SUCCESS: restore and external read-back verified; artifact {final}")
        return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Private env file; not evaluated as shell code")
    parser.add_argument("--local-only", action="store_true", help="Isolated rehearsal only; never production success")
    parser.add_argument("--keep", action="store_true", help="Retain a separate pre-deployment copy outside daily rotation")
    parser.add_argument("--verify-archive", type=Path, help="Decrypt and restore an existing backup.tar.age in isolation")
    parser.add_argument("--identity", type=Path, help="Offsite age private key for --verify-archive only")
    parser.add_argument("--restore-image", help="Already-present PostgreSQL image for recovery on another computer")
    args = parser.parse_args()
    try:
        if args.config:
            load_env(args.config)
        if args.verify_archive:
            if not args.identity or args.local_only or args.keep:
                raise BackupError("Archive recovery requires --identity and cannot combine with --local-only/--keep")
            verify_archive(Config(offline_verify=True), args.verify_archive, args.identity, args.restore_image)
        else:
            if args.identity or args.restore_image:
                raise BackupError("--identity/--restore-image require --verify-archive")
            backup(Config(args.local_only), keep=args.keep)
        return 0
    except (BackupError, ValueError, OSError, KeyError, tarfile.TarError) as exc:
        # Expected errors contain no raw stderr/config values. Preserve pending artifacts.
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
