"""Create server-only production secrets for a new Meken deployment."""
import argparse
import ipaddress
import json
import os
import re
from pathlib import Path
from secrets import token_hex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--image-tag", required=True)
args = parser.parse_args()
try:
    ipaddress.ip_address(args.host)
except ValueError:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", args.host):
        parser.error("host must be an IP address or DNS hostname")
if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.image_tag):
    parser.error("invalid image tag")

app_password, migrator_password = token_hex(24), token_hex(24)
base = "https://" + args.host
values = {
    "POSTGRES_PASSWORD": token_hex(24),
    "MEKEN_APP_DB_PASSWORD": app_password,
    "MEKEN_MIGRATOR_DB_PASSWORD": migrator_password,
    "MEKEN_DATABASE_URL": f"postgresql+asyncpg://meken_app:{app_password}@db:5432/meken",
    "MEKEN_MIGRATION_DATABASE_URL": f"postgresql+asyncpg://meken_migrator:{migrator_password}@db:5432/meken",
    "MEKEN_METRICS_TOKEN": token_hex(24),
    "MEKEN_ENV": "production",
    "MEKEN_AI_ENABLED": "false",
    "MEKEN_OPENAI_API_KEY": "",
    "MEKEN_AI_MODEL": "gpt-4.1-mini",
    "MEKEN_PROVIDER_CONFIG": "data/providers.bi-group.json",
    "MEKEN_ALLOWED_HOSTS": json.dumps([args.host, "localhost", "127.0.0.1"]),
    "MEKEN_PRIVACY_URL": base + "/privacy",
    "MEKEN_TERMS_URL": base + "/terms",
    "MEKEN_API_PORT": "8001",
    "MEKEN_IMAGE_TAG": args.image_tag,
    "MEKEN_DOCKER_SUBNET": "172.30.64.0/24",
    "MEKEN_DOCKER_GATEWAY": "172.30.64.1",
    "FORWARDED_ALLOW_IPS": "172.30.64.1",
}
with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
    file.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Created production configuration with new secrets; AI is disabled.")
