import json
import logging

from prometheus_client import Counter, Histogram

AI_CALLS = Counter("meken_ai_calls_total", "Model calls", ["purpose", "outcome"])
AI_TOKENS = Counter("meken_ai_tokens_total", "Model tokens", ["kind"])
PROVIDER_CALLS = Counter(
    "meken_provider_calls_total", "Provider fetch outcomes", ["provider", "outcome"]
)
PROVIDER_LATENCY = Histogram("meken_provider_seconds", "Provider fetch latency", ["provider"])
TURNS = Counter("meken_turns_total", "Chat turns", ["outcome"])
HTTP_LATENCY = Histogram("meken_http_seconds", "HTTP duration", ["method", "route", "status"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # No prompts, tokens, provider bodies, URLs with secrets, or exception messages.
        return json.dumps({"level": record.levelname, "event": record.getMessage()}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.getLogger("meken").handlers = [handler]
    logging.getLogger("meken").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
