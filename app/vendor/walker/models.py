"""The two models, both on Cloudflare Workers AI: the navigator (clef, a System One classifier: it makes choices,
writes no text) and the text model (DeepSeek V4.1 Flash: writes a search query or the answer). As in jev-ultrafast's
model.py, every model answer is validated before it can act: a choice must be one of the offered keys with
probabilities that sum to one, and text must be a JSON object of the expected keys."""
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("CF_ENV_FILE", r"C:\Users\STEFFAN\Desktop\medit\tib-chat\.env"))
NAVIGATOR = os.environ.get("NAVIGATOR", "@cf/cloudflare/clef")
WRITER = os.environ.get("WRITER", "@cf/deepseek-ai/deepseek-v4.1-flash-1017")
WRITER_EFFORT = os.environ.get("WRITER_EFFORT", "high")  # the writer's reasoning effort for the answer
QUERY_EFFORT = os.environ.get("QUERY_EFFORT", "low")  # and for a search query, a short rewrite of the question
# USD per million input / output tokens (Cloudflare Workers AI model pages, read 31 Oct 2026); classifiers bill input only
PRICES = {"@cf/cloudflare/clef": (0.24, 0.0), "@cf/cloudflare/clef-flash": (0.038, 0.0), "typesafe/jev": (0.042, 0.0),
          "@cf/deepseek-ai/deepseek-v4.1-flash-1017": (0.30, 1.20), "@cf/openai/gpt-oss-120b": (0.35, 0.75),
          "@cf/deepseek-ai/deepseek-v4-pro-0813": (1.32, 3.96), "@cf/zai-org/glm-5.3-flash": (0.15, 0.50), "@cf/google/embeddinggemma-300m": (0.0, 0.0), "@cf/qwen/qwen3.8-27b": (0.45, 3.20),
          "baseten/joey-1": (0.01, 0.0), "baseten/elephant-1": (0.035, 0.0), "baseten/mammoth-1": (0.10, 0.0)}


# Workers AI throttles bursts (HTTP 429, code 971): calls of this process share a small number of slots
_SLOTS = threading.Semaphore(int(os.environ.get("MODEL_SLOTS", 3)))


class ModelError(Exception):
    """A model call failed or its answer was invalid; no action follows from it."""


def _credentials() -> tuple[str, str]:
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):  # a deployed service has them in its environment
        return os.environ["CF_ACCOUNT_ID"].strip().strip('"'), os.environ["CF_API_TOKEN"].strip().strip('"')
    env = dict(line.split("=", 1) for line in ENV_FILE.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    return env["CF_ACCOUNT_ID"].strip().strip('"'), env["CF_API_TOKEN"].strip().strip('"')


def _post(url: str, body: dict, attempts: int = 8) -> dict:
    account, token = _credentials()
    data = json.dumps(body).encode()
    for attempt in range(attempts):
        req = urllib.request.Request(url.format(account=account), data=data, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with _SLOTS, urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504, 529) and attempt < attempts - 1:
                time.sleep(min(60, 3 * 2**attempt))
                continue
            raise ModelError(f"HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts - 1:
                time.sleep(min(30, 2 * 2**attempt))
                continue
            raise ModelError(f"connection failed: {e}") from None
    raise ModelError("unreachable")


def validate_choice(answer: dict, keys) -> dict:
    """jev-ultrafast's check: the choice is an offered key, probabilities cover exactly the keys and sum to one."""
    try:
        probs = answer["probabilities"]
        valid = (answer["choice"] in keys and set(probs) == set(keys)
                 and all(isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in probs.values())
                 and abs(sum(probs.values()) - 1) < 0.02 and probs[answer["choice"]] >= max(probs.values()) - 1e-6)
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ModelError("invalid choice answer; no action executed")
    return answer


def classify(state: dict, questions: dict) -> tuple[dict, dict]:
    """One System One request: all questions on one state. Returns the answers and usage (tokens, USD)."""
    model = NAVIGATOR
    short = model.rsplit("/", 1)[-1]
    started = time.perf_counter()
    wire = {k: ({**q, "type": "noul"} if q.get("type") == "bool" else q) for k, q in questions.items()}  # System One's name for yes/no
    res = _post(f"https://api.cloudflare.com/client/v4/accounts/{{account}}/ai/run/{model}",
                {"model": short, "state": state, "questions": wire})
    if res.get("success") is False:
        raise ModelError(f"classifier error: {str(res.get('errors'))[:300]}")
    result = res.get("result") or {}
    if "answers" not in result and isinstance(result.get("result"), dict):  # third-party models wrap a run record
        result = result["result"]
    usage = result.get("usage") or {}
    tokens = int(usage.get("input_tokens") or 0)
    return result.get("answers") or {}, {"model": model, "input_tokens": tokens, "usd": tokens * PRICES.get(model, (0, 0))[0] / 1e6,
                                         "ms": round((time.perf_counter() - started) * 1000)}


def write_json(system: str, content: dict, keys: set[str], max_tokens: int = 6000, effort: str | None = None) -> tuple[dict, dict]:
    """One text-model call that must return a JSON object with exactly `keys`."""
    started = time.perf_counter()
    effort = effort or WRITER_EFFORT
    body = {"model": WRITER, "temperature": 0, "max_tokens": max_tokens, "reasoning_effort": effort,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(content, ensure_ascii=False)}]}
    spent = 0.0
    efforts = ([effort] + [e for e in ("low", "none") if e != effort] + ["none", "none"])[:3]  # a reply cut at the token limit is retried with less, then without reasoning
    for attempt in range(3):
        body = {k: v for k, v in body.items() if k != "reasoning_effort"}
        if efforts[attempt]:
            body["reasoning_effort"] = efforts[attempt]
        res = _post("https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions", body)
        text = (res.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        u = res.get("usage") or {}
        pin, pout = PRICES.get(WRITER, (0, 0))
        usage = {"model": WRITER, "input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                 "usd": spent + ((u.get("prompt_tokens") or 0) * pin + (u.get("completion_tokens") or 0) * pout) / 1e6,
                 "ms": round((time.perf_counter() - started) * 1000)}
        try:
            out = json.loads(text[text.find("{"): text.rfind("}") + 1])
            if isinstance(out, dict) and keys <= set(out):  # extra keys are ignored, missing ones are an error
                return out, usage
        except ValueError:
            pass
        spent = usage["usd"]
        last = f'finish_reason={ (res.get("choices") or [{}])[0].get("finish_reason")!r}, output_tokens={u.get("completion_tokens")}, text={text[-200:]!r}'
    raise ModelError(f"text model returned no valid JSON object; nothing used ({last})")


OPENROUTER_ENV = Path(os.environ.get("OPENROUTER_ENV_FILE", str(Path.home() / "Cloud-Zugang-ausserhalb-des-Repos" / "openrouter.env")))


def _openrouter_key() -> str:
    """OPENROUTER_API_KEY from the environment or the credentials file (never logged)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and OPENROUTER_ENV.exists():
        for line in OPENROUTER_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise ModelError("no OPENROUTER_API_KEY")
    return key


def systemone_openrouter(model: str, state: dict, questions: dict, attempts: int = 8) -> dict:
    """One System One decision request on OpenRouter (POST /api/v1/systemone: state plus typed questions, the same
    contract as the Cloudflare decision models). Returns the response: {"answers", "usage"}."""
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    for attempt in range(attempts):
        req = urllib.request.Request("https://openrouter.ai/api/v1/systemone", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + _openrouter_key(), "Content-Type": "application/json"})
        try:
            with _SLOTS, urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(min(30, 2 ** attempt))
                continue
            raise ModelError(f"OpenRouter HTTP {e.code}: {e.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts - 1:
                time.sleep(min(30, 2 ** attempt))
                continue
            raise ModelError(f"OpenRouter unreachable: {e}")
    raise ModelError("OpenRouter: no answer")
