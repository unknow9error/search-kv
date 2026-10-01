"""Creates local development secrets only; never overwrites an existing .env."""
import os
from pathlib import Path
from secrets import token_hex

root = Path(__file__).resolve().parents[1]
path = root / ".env"
contents = (root / ".env.example").read_text().replace("replace-with-a-random-password", token_hex(24)).replace("replace-with-a-random-token-at-least-32-chars", token_hex(24))
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
    file.write(contents)
print("Created .env with random local secrets; existing files are never overwritten.")
