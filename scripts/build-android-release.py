#!/usr/bin/env python3
"""Build a signed Meken release using a dedicated, ignored local upload key.

Generating a key is explicit and never overwrites existing credentials. Secrets
are passed through environment variables and never printed or put in argv.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SIGNING = ROOT / "android" / "signing"
CREDENTIALS = SIGNING / "release-signing.json"
KEYSTORE = SIGNING / "meken-upload.keystore"


def generate_key():
    if CREDENTIALS.exists() or KEYSTORE.exists():
        raise RuntimeError("A Meken signing key already exists; nothing was overwritten.")
    keytool = shutil.which("keytool")
    java = os.environ.get("JAVA_HOME")
    if java and (Path(java) / "bin/keytool").exists():
        keytool = str(Path(java) / "bin/keytool")
    if not keytool:
        raise RuntimeError("JDK 17 keytool is required.")
    SIGNING.mkdir(mode=0o700, parents=True, exist_ok=True)
    SIGNING.chmod(0o700)
    password = secrets.token_urlsafe(32)
    values = {
        "MEKEN_ANDROID_KEYSTORE_FILE": str(KEYSTORE),
        "MEKEN_ANDROID_KEYSTORE_PASSWORD": password,
        "MEKEN_ANDROID_KEY_ALIAS": "meken-upload",
        "MEKEN_ANDROID_KEY_PASSWORD": password,
    }
    child_env = os.environ | values
    # The key and credential files are private before any content is written.
    previous_umask = os.umask(0o077)
    try:
        subprocess.run([
            keytool, "-genkeypair", "-keystore", str(KEYSTORE),
            "-storetype", "PKCS12", "-alias", "meken-upload", "-keyalg", "RSA",
            "-keysize", "3072", "-validity", "10000",
            "-dname", "CN=Meken Android upload key,C=KZ",
            "-storepass:env", "MEKEN_ANDROID_KEYSTORE_PASSWORD",
            "-keypass:env", "MEKEN_ANDROID_KEY_PASSWORD",
        ], env=child_env, check=True)
        KEYSTORE.chmod(0o600)
        descriptor = os.open(CREDENTIALS, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(values, output)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.umask(previous_umask)
    print("Dedicated Meken upload key prepared in ignored android/signing (private permissions).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", action="store_true")
    parser.add_argument("--key-only", action="store_true")
    args = parser.parse_args()
    if args.generate_key:
        generate_key()
    if args.key_only:
        return
    if not CREDENTIALS.is_file():
        raise RuntimeError("Configure signing variables, or explicitly generate a new dedicated key first.")
    values = json.loads(CREDENTIALS.read_text())
    expected = {"MEKEN_ANDROID_KEYSTORE_FILE", "MEKEN_ANDROID_KEYSTORE_PASSWORD",
                "MEKEN_ANDROID_KEY_ALIAS", "MEKEN_ANDROID_KEY_PASSWORD"}
    if set(values) != expected or any(not isinstance(value, str) or not value for value in values.values()):
        raise RuntimeError("Invalid local signing configuration.")
    subprocess.run([str(ROOT / "android/gradlew"), "--no-daemon", "--console=plain",
                    ":app:assembleRelease", ":app:bundleRelease"],
                   cwd=ROOT / "android", env=os.environ | values, check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        sys.exit(1)
