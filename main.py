"""
main.py
=========================
End-to-end pipeline:
  Phase 1  Load & inspect Excel data
  Phase 2  Preprocess / deduplicate item descriptions
  Phase 3  Stratified category discovery (parallel API batches)
  Phase 4  Taxonomy consolidation (single API call)
  Phase 5  Full parallel item classification (cached, resumable)
  Phase 6  Write labels back to Excel
  Phase 7  Validation summary

Usage
-----
  export OPENAI_API_KEY=sk-...
  python main.py

Requirements
------------
  pip install "openai>=1.30" pandas openpyxl tiktoken ftfy tqdm
"""

# ══════════════════════════════════════════════════════════════════════════════
#  STDLIB
# ══════════════════════════════════════════════════════════════════════════════
from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import logging
import math
import os
import random
import re
import time
import unicodedata
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ══════════════════════════════════════════════════════════════════════════════
#  THIRD-PARTY
# ══════════════════════════════════════════════════════════════════════════════
import ftfy
import pandas as pd
import tiktoken
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from tqdm import tqdm


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 0 — CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ModelConfig:
    """
    Per-model API capabilities and rate-limit envelope.

    Reasoning-model differences (GPT-5 family, o-series)
    -----------------------------------------------------
    These models perform hidden chain-of-thought before emitting any visible
    output.  The final answer always arrives in choices[0].message.content —
    reasoning tokens are completely separate and only appear in the usage
    object under completion_tokens_details.reasoning_tokens.  They are never
    mixed into message.content, so the content extraction logic is identical
    to standard models; we simply need to use the correct API parameters:

      use_completion_tokens = True   ->  send max_completion_tokens (not max_tokens).
                                         The budget is shared between hidden reasoning
                                         and visible output.  Setting it too low causes
                                         finish_reason='length' with empty content.
      supports_temperature  = False  ->  the API rejects the temperature parameter.
      system_role           = "developer"  ->  required for instruction messages on
                                               GPT-5 / o-series; "system" is rejected.
      is_reasoning          = True   ->  enables TPM headroom reservation (reasoning
                                         tokens are billed but invisible up-front).
    """

    model_id: str
    tpm_limit: int
    rpm_limit: int
    max_out_tokens: int
    encoding: str = "cl100k_base"
    base_url: Optional[str] = None
    # Reasoning-model flags
    is_reasoning: bool = False
    system_role: str = "system"
    use_completion_tokens: bool = False
    supports_temperature: bool = True
    extra_kwargs: dict = field(default_factory=dict)


MODEL_REGISTRY: dict[str, ModelConfig] = {
    # ── GPT-5 family (reasoning) ──────────────────────────────────────────
    "gpt-5-mini": ModelConfig(
        model_id="gpt-5-mini",
        tpm_limit=500_000,  # verify at platform.openai.com/account/limits
        rpm_limit=500,
        max_out_tokens=16_384,  # shared reasoning + visible output budget
        encoding="o200k_base",
        is_reasoning=True,
        system_role="developer",  # required for GPT-5 / o-series
        use_completion_tokens=True,  # must use max_completion_tokens
        supports_temperature=False,  # temperature param not accepted
    ),
    # ── GPT-4o family (standard) ──────────────────────────────────────────
    "gpt-4o-mini": ModelConfig(
        model_id="gpt-4o-mini",
        tpm_limit=200_000,
        rpm_limit=500,
        max_out_tokens=4_096,
        encoding="o200k_base",
    ),
    "gpt-4o": ModelConfig(
        model_id="gpt-4o",
        tpm_limit=30_000,
        rpm_limit=500,
        max_out_tokens=4_096,
        encoding="o200k_base",
    ),
    # ── GPT-3.5 (high throughput) ─────────────────────────────────────────
    "gpt-3.5-turbo": ModelConfig(
        model_id="gpt-3.5-turbo",
        tpm_limit=1_000_000,
        rpm_limit=3_500,
        max_out_tokens=4_096,
        encoding="cl100k_base",
    ),
}

# ── Active model ← change only this line to swap models ──────────────────────
ACTIVE_MODEL: str = "gpt-5-mini"

# ── API credentials ───────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY")


# ── File paths ────────────────────────────────────────────────────────────────
INPUT_FILE = "raw_data.xlsx"
INPUT_SHEET = "Raw Data"
ITEM_COLUMN = "Item"
OUTPUT_FILE = "gpt_5_mini_raw_data_clustered.xlsx"
CACHE_FILE = "gpt_5_mini_classification_cache.json"
TAXONOMY_FILE = "gpt_5_mini_taxonomy.json"

# ── Processing knobs ──────────────────────────────────────────────────────────
BATCH_SIZE = 50  # items per API call (classification)
DISCOVERY_BATCH = 40  # items per call (discovery)
MAX_CONCURRENT = 8  # max parallel in-flight requests
CHECKPOINT_EVERY = 30  # flush cache every N completed batches
MAX_RETRIES = 6  # max retry attempts per request before giving up
BASE_RETRY_DELAY = 1.0  # seconds; doubled each retry attempt + jitter
RATE_LIMIT_MARGIN = 0.90  # stay at most 90 % of the declared TPM / RPM limit

# ── Category discovery ────────────────────────────────────────────────────────
DISCOVERY_SAMPLE = 3_000  # items in stratified discovery sample
DISCOVERY_ROUNDS = 2  # sweeps over the sample

REFERENCE_CATEGORIES = [
    "lubricant",
    "fastener",
    "stationery",
    "ppe",
    "electrical component",
    "chemical",
    "hand tool",
    "pipe fitting",
    "consumable",
    "civil construction",
    "mechanical equipment",
    "it hardware",
    "it service",
    "marketing material",
    "professional service",
    "facility service",
    "laboratory item",
    "packaging material",
    "electrical service",
    "other",
]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — LOGGING
# ══════════════════════════════════════════════════════════════════════════════


def _setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("pipeline.log", mode="w", encoding="utf-8"),
        ],
    )
    return logging.getLogger("procurement")


log: logging.Logger = _setup_logging()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — TOKEN COUNTING
# ══════════════════════════════════════════════════════════════════════════════

_ENCODERS: dict[str, tiktoken.Encoding] = {}


def _get_encoder(name: str) -> tiktoken.Encoding:
    if name not in _ENCODERS:
        _ENCODERS[name] = tiktoken.get_encoding(name)
    return _ENCODERS[name]


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    try:
        return len(_get_encoder(encoding).encode(text))
    except Exception:
        return max(1, len(text) // 4)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — RATE LIMITER
# ══════════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """
    Async dual sliding-window rate limiter enforcing RPM and TPM simultaneously.

    Before every API call: await limiter.acquire(estimated_tokens)
    The limiter sleeps until BOTH windows have room, then records the event.
    """

    def __init__(self, rpm: int, tpm: int, margin: float = RATE_LIMIT_MARGIN):
        self._rpm_cap = max(1, int(rpm * margin))
        self._tpm_cap = max(1, int(tpm * margin))
        self._req_ts: deque[float] = deque()
        self._tok_log: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        self._window = 60.0

    def _evict(self, now: float) -> None:
        cutoff = now - self._window
        while self._req_ts and self._req_ts[0] <= cutoff:
            self._req_ts.popleft()
        while self._tok_log and self._tok_log[0][0] <= cutoff:
            self._tok_log.popleft()

    async def acquire(self, n_tokens: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._evict(now)
                rpm_ok = len(self._req_ts) < self._rpm_cap
                tok_used = sum(t for _, t in self._tok_log)
                tpm_ok = tok_used + n_tokens <= self._tpm_cap

                if rpm_ok and tpm_ok:
                    self._req_ts.append(now)
                    self._tok_log.append((now, n_tokens))
                    return

                rpm_wait = 0.0
                if not rpm_ok and self._req_ts:
                    rpm_wait = self._window - (now - self._req_ts[0]) + 0.05

                tpm_wait = 0.0
                if not tpm_ok and self._tok_log:
                    cum = 0
                    for ts, tok in self._tok_log:
                        cum += tok
                        if tok_used - cum + n_tokens <= self._tpm_cap:
                            tpm_wait = self._window - (now - ts) + 0.05
                            break
                    else:
                        tpm_wait = self._window - (now - self._tok_log[0][0]) + 0.05

                wait = max(rpm_wait, tpm_wait, 0.05)
                log.debug(
                    "RateLimiter sleeping %.2fs (rpm_ok=%s tpm_ok=%s)",
                    wait,
                    rpm_ok,
                    tpm_ok,
                )
                await asyncio.sleep(wait)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — API CLIENT + RETRY
# ══════════════════════════════════════════════════════════════════════════════


class IncompleteOutputError(Exception):
    """
    Raised when finish_reason != 'stop'.

    For reasoning models the max_completion_tokens budget is shared between
    hidden reasoning and visible output. If the model exhausts the budget
    during its internal thinking phase it returns finish_reason='length' with
    message.content = None. Raising here lets with_retry treat this as a
    transient failure so the batch is retried rather than silently producing
    empty content that later breaks JSON parsing.
    """


_RETRYABLE = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    IncompleteOutputError,
)


async def with_retry(
    coro_fn, *args, tag: str = "", retries: int = MAX_RETRIES, **kwargs
):
    """
    Call coro_fn(*args, **kwargs) with exponential backoff + jitter.

    Returns the result on success, or None after all retries are exhausted.
    BadRequestError (400) is not retried — it indicates a prompt issue.
    RateLimitError respects the retry-after header when present.
    """
    delay = BASE_RETRY_DELAY
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn(*args, **kwargs)

        except BadRequestError as exc:
            log.warning("[%s] BadRequest (400) — not retrying: %s", tag, exc)
            return None

        except RateLimitError as exc:
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:
                retry_after = exc.response.headers.get("retry-after")
            sleep_for = float(retry_after) if retry_after else delay
            log.warning(
                "[%s] RateLimit attempt %d/%d — sleeping %.1fs",
                tag,
                attempt,
                retries,
                sleep_for,
            )
            await asyncio.sleep(sleep_for + random.uniform(0, 0.5))
            delay = min(delay * 2, 60)

        except _RETRYABLE as exc:
            sleep_for = delay + random.uniform(0, delay * 0.3)
            log.warning(
                "[%s] %s attempt %d/%d — sleeping %.1fs",
                tag,
                type(exc).__name__,
                attempt,
                retries,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2, 60)

        except Exception as exc:
            log.error("[%s] Unexpected error: %s", tag, exc)
            return None

    log.error("[%s] All %d retries exhausted.", tag, retries)
    return None


def make_client(cfg: ModelConfig) -> AsyncOpenAI:
    """Create an AsyncOpenAI client for the given model config."""
    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return AsyncOpenAI(**kwargs)


def build_api_params(
    cfg: ModelConfig,
    system_prompt: str,
    user_content: str,
    max_output_tokens: int,
    temperature: float = 0.1,
) -> dict:
    """
    Build the kwargs dict for client.chat.completions.create().

    Handles the following differences between standard and reasoning models:

    Parameter            Standard model        Reasoning model (GPT-5, o-series)
    ───────────────────  ───────────────────   ──────────────────────────────────
    System-msg role      "system"              "developer"
    Token-limit key      max_tokens            max_completion_tokens
    Token-limit value    per-call estimate     full cfg.max_out_tokens (shared budget)
    temperature          included              OMITTED (API rejects it)

    The final visible answer is always in choices[0].message.content for
    both model types. Reasoning tokens never appear in message.content.
    """
    messages = [
        {"role": cfg.system_role, "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    params: dict = {
        "model": cfg.model_id,
        "messages": messages,
        **cfg.extra_kwargs,
    }

    # Token limit: reasoning models must use max_completion_tokens and always
    # receive the full budget (we cannot predict how many tokens reasoning will
    # consume, and shrinking the budget risks a 'length' finish_reason).
    token_key = "max_completion_tokens" if cfg.use_completion_tokens else "max_tokens"
    token_value = cfg.max_out_tokens if cfg.is_reasoning else max_output_tokens
    params[token_key] = token_value

    # Temperature: reasoning models reject this parameter entirely.
    if cfg.supports_temperature:
        params["temperature"] = temperature

    return params


def extract_content(resp, cfg: ModelConfig, tag: str = "") -> str:
    """
    Extract the final answer text from a chat.completions response.

    For ALL model types — standard and reasoning — the visible output lives
    exclusively in choices[0].message.content.

    Reasoning tokens (if any) appear only in:
        resp.usage.completion_tokens_details.reasoning_tokens
    They are NEVER part of message.content and require no special handling here.

    The only failure mode specific to reasoning models is finish_reason='length':
    the shared token budget was exhausted during internal reasoning before any
    visible output was written. We raise IncompleteOutputError so with_retry
    can retry the request rather than propagating empty content downstream.
    """
    choice = resp.choices[0]
    finish_reason = choice.finish_reason

    if finish_reason != "stop":
        reasoning_tok = 0
        usage = getattr(resp, "usage", None)
        if usage:
            details = getattr(usage, "completion_tokens_details", None)
            if details:
                reasoning_tok = getattr(details, "reasoning_tokens", 0) or 0
        raise IncompleteOutputError(
            f"[{tag}] finish_reason='{finish_reason}' (expected 'stop'). "
            f"Reasoning tokens used: {reasoning_tok}. "
            f"Increase max_out_tokens in ModelConfig if this recurs."
        )

    content = choice.message.content
    if not content:
        log.warning("[%s] finish_reason='stop' but message.content is empty.", tag)
        return ""
    return content


def reasoning_headroom(cfg: ModelConfig, visible_output_tokens: int) -> int:
    """
    Extra TPM capacity to reserve for hidden reasoning tokens.

    Reasoning models bill internal reasoning tokens against the TPM limit
    even though those tokens are not visible. The ratio is typically 2-4x
    the visible output; we use 3x as a conservative estimate.
    Standard models have no reasoning tokens so this returns 0.
    """
    return visible_output_tokens * 3 if cfg.is_reasoning else 0


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — JSON EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════


def extract_json(text: Optional[str]) -> Optional[Any]:
    """
    Robustly extract the first valid JSON array or object from LLM output.

    Strategies attempted in order:
      1. Direct parse of stripped text.
      2. Strip markdown fences (```json ... ```), retry.
      3. Bracket-walk: locate the first [ or { and find its matching closer.
      4. Fix trailing-comma artifacts, retry.
    Returns None if all strategies fail.
    """
    if not text:
        return None

    for candidate in (text.strip(), text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    stripped = (
        re.sub(r"```(?:json|python|text|JSON)?", "", text).replace("```", "").strip()
    )
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for open_ch, close_ch in ("[", "]"), ("{", "}"):
        start = stripped.find(open_ch)
        if start == -1:
            continue
        depth = end_idx = 0
        in_str = esc = False
        for i, ch in enumerate(stripped[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
            if not in_str:
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
        if end_idx:
            chunk = re.sub(r",\s*([}\]])", r"\1", stripped[start:end_idx])
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                pass
    return None


def extract_string_list(text: Optional[str]) -> list[str]:
    """Return a flat, lowercased list of strings from LLM output."""
    parsed = extract_json(text)
    if isinstance(parsed, list):
        return [str(s).strip().lower() for s in parsed if s and str(s).strip()]
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return [str(s).strip().lower() for s in v if s and str(s).strip()]
    # Last resort: pull any quoted phrase of 2-6 words
    return [
        m.strip().lower()
        for m in re.findall(r'"([^"]{3,60})"', text or "")
        if 2 <= len(m.split()) <= 6
    ]


def extract_classification_list(text: Optional[str], expected: int) -> Optional[list]:
    """
    Return a list of classification dicts from LLM output.
    Falls back to line-by-line object recovery when full parse fails.
    The returned list may be shorter than `expected`; callers pad/trim.
    """
    parsed = extract_json(text)
    if isinstance(parsed, list) and parsed:
        return parsed

    if text:
        objects = []
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    objects.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if objects:
            log.debug("Line-by-line recovery: %d/%d objects", len(objects), expected)
            return objects
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — ASYNC BATCH RUNNER
# ══════════════════════════════════════════════════════════════════════════════


async def run_parallel(
    coroutines: list,
    max_concurrent: int = MAX_CONCURRENT,
    desc: str = "Processing",
) -> list:
    """
    Execute coroutines with bounded concurrency via a semaphore.
    Results are returned in original order; per-task exceptions are captured.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results = [None] * len(coroutines)
    pbar = tqdm(total=len(coroutines), desc=desc, unit="batch", dynamic_ncols=True)

    async def _guarded(idx: int, coro):
        async with semaphore:
            try:
                results[idx] = await coro
            except Exception as exc:
                log.error("Unhandled exception in batch slot %d: %s", idx, exc)
            finally:
                pbar.update(1)

    await asyncio.gather(*[_guarded(i, c) for i, c in enumerate(coroutines)])
    pbar.close()
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

_RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_ZWSP = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_RE_MULTISPC = re.compile(r"[ \t]{2,}")
_RE_DIGITS_ONLY = re.compile(r"^[\d\s\-\.\/,\+]+$")
_RE_TAX = re.compile(r"\b(gst|cgst|sgst|igst|tds|vat|tax)\b", re.I)
_RE_CODE_ONLY = re.compile(r"^[A-Z0-9\-_/\.]{2,25}$")
_SKIP_VALUES = frozenset({"", "nan", "none", "null", "na", "n/a", "-", "."})


def clean_text(raw: str) -> str:
    """Fix encoding, strip control characters, and collapse whitespace."""
    if not raw:
        return ""
    text = ftfy.fix_text(raw)
    text = _RE_CTRL.sub(" ", text)
    text = _RE_ZWSP.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = _RE_MULTISPC.sub(" ", text).strip()
    return text


def get_flags(text: str) -> dict:
    """Assign boolean quality flags used for deterministic post-processing."""
    upper = text.upper()
    return {
        "too_short": len(text) <= 3,
        "digits_only": bool(_RE_DIGITS_ONLY.match(text)),
        "code_only": bool(_RE_CODE_ONLY.match(upper)) and text == upper,
        "tax_noise": bool(_RE_TAX.search(text)),
        "long_text": len(text) > 200,
    }


def build_item_registry(item_series: pd.Series, cfg: ModelConfig) -> dict:
    """
    Build the canonical item registry from the raw item column.

    Deduplication: items that differ only in case / whitespace after cleaning
    are collapsed to a single canonical entry (first occurrence).

    Returns a dict: canonical_raw_item -> {cleaned, lower, flags, tokens, ...}
    """
    registry: dict[str, dict] = {}
    lower_to_canon: dict[str, str] = {}
    flag_totals: Counter = Counter()
    skipped: list[str] = []

    for raw in tqdm(item_series, desc="Preprocessing", unit="rows", leave=False):
        raw_s = str(raw).strip()
        if raw_s.lower() in _SKIP_VALUES:
            skipped.append(raw_s)
            continue

        cleaned = clean_text(raw_s)
        if not cleaned or cleaned.lower() in _SKIP_VALUES:
            skipped.append(raw_s)
            continue

        lower_key = cleaned.lower()
        if lower_key not in lower_to_canon:
            lower_to_canon[lower_key] = raw_s

        canonical = lower_to_canon[lower_key]
        if canonical not in registry:
            flags = get_flags(cleaned)
            for f, v in flags.items():
                if v:
                    flag_totals[f] += 1
            registry[canonical] = {
                "cleaned": cleaned,
                "lower": lower_key,
                "flags": flags,
                "tokens": count_tokens(cleaned, cfg.encoding),
                "category": None,
                "confidence": None,
            }

    log.info(
        "Preprocessing done: unique=%s  skipped=%s  near-dups=%s",
        f"{len(registry):,}",
        f"{len(skipped):,}",
        f"{(item_series != '').sum() - len(registry):,}",
    )
    log.info("Edge-case flags: %s", dict(flag_totals.most_common()))
    return registry


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — PHASE 3: CATEGORY DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

_DISC_SYSTEM = (
    "You are an expert in procurement item taxonomy.\n"
    "Given a batch of procurement item descriptions, analyze them and identify the "
    "most relevant PRODUCT or SERVICE CATEGORIES they belong to.\n\n"
    "Output ONLY a JSON array of category name strings. For example:\n"
    '["fastener", "stationery", "lubricant", "electrical component", "civil construction service"]\n\n'
    "Guidelines:\n"
    "- Each category should be a GENERIC noun phrase (1-5 words), all lowercase.\n"
    "- Focus on what the item IS, not how it might be used.\n"
    "- Consolidate synonyms and similar concepts into a single category "
    "(e.g., bolt/nut/screw → fastener; grease/oil → lubricant).\n"
    "- Return between 5 and 15 distinct categories for this batch — no fewer, no more.\n"
    "- Do NOT include sizes, grades, brands, supplier names, or other qualifiers in category names.\n"
    "- Return ONLY the JSON array with no additional text, explanation, markdown, or formatting."
)


def build_stratified_sample(
    df_raw: pd.DataFrame,
    item_registry: dict,
    n: int = DISCOVERY_SAMPLE,
) -> list[str]:
    """
    Stratified sample of unique items, proportional to Commodity Group size,
    with a minimum of 10 items per group to avoid category blind spots.
    """
    random.seed(42)
    grp_col = "Commodity Group"
    unique_items = list(item_registry.keys())

    if grp_col not in df_raw.columns:
        log.warning("'%s' not found — using random sample.", grp_col)
        return random.sample(unique_items, min(n, len(unique_items)))

    item_to_grp: dict[str, str] = {}
    for _, row in df_raw[[ITEM_COLUMN, grp_col]].dropna().iterrows():
        k = str(row[ITEM_COLUMN]).strip()
        if k in item_registry and k not in item_to_grp:
            item_to_grp[k] = str(row[grp_col])

    grp_buckets: dict[str, list[str]] = defaultdict(list)
    for item, grp in item_to_grp.items():
        grp_buckets[grp].append(item)

    total = sum(len(v) for v in grp_buckets.values()) or 1
    sampled: list[str] = []
    for grp, bucket in sorted(grp_buckets.items(), key=lambda x: -len(x[1])):
        quota = max(10, int(n * len(bucket) / total))
        sampled.extend(random.sample(bucket, min(quota, len(bucket))))

    # Deduplicate preserving order
    seen: set[str] = set()
    sampled = [x for x in sampled if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]

    if len(sampled) < n:
        rest = [i for i in unique_items if i not in seen]
        sampled += random.sample(rest, min(n - len(sampled), len(rest)))

    random.shuffle(sampled)
    return sampled[:n]


async def _disc_batch(
    client: AsyncOpenAI,
    cfg: ModelConfig,
    rate_limiter: RateLimiter,
    item_registry: dict,
    batch: list[str],
    idx: int,
) -> list[str]:
    cleaned = [item_registry[i]["cleaned"] for i in batch if i in item_registry]
    if not cleaned:
        return []

    user_content = "Procurement items:\n" + json.dumps(cleaned, ensure_ascii=False)
    n_input_tok = count_tokens(_DISC_SYSTEM + user_content, cfg.encoding)
    out_budget = 512
    await rate_limiter.acquire(
        n_input_tok + out_budget + reasoning_headroom(cfg, out_budget)
    )

    params = build_api_params(
        cfg, _DISC_SYSTEM, user_content, out_budget, temperature=0.1
    )

    async def _call():
        resp = await client.chat.completions.create(**params)
        return extract_content(resp, cfg, tag=f"disc-{idx}")

    raw = await with_retry(_call, tag=f"disc-{idx}")
    cats = extract_string_list(raw)
    if not cats:
        log.debug("Discovery batch %d: no categories extracted.", idx)
    return cats


async def run_discovery(
    df_raw: pd.DataFrame,
    item_registry: dict,
    cfg: ModelConfig,
    rate_limiter: RateLimiter,
) -> list[str]:
    """
    Run DISCOVERY_ROUNDS stratified-sample sweeps; return deduplicated candidates.
    """
    sample = build_stratified_sample(df_raw, item_registry, DISCOVERY_SAMPLE)
    log.info("Discovery sample: %d items", len(sample))

    client = make_client(cfg)
    all_cats: list[str] = []

    for rnd in range(1, DISCOVERY_ROUNDS + 1):
        random.seed(rnd * 17)
        random.shuffle(sample)
        batches = [
            sample[i : i + DISCOVERY_BATCH]
            for i in range(0, len(sample), DISCOVERY_BATCH)
        ]
        coros = [
            _disc_batch(client, cfg, rate_limiter, item_registry, b, i)
            for i, b in enumerate(batches)
        ]
        results = await run_parallel(
            coros,
            max_concurrent=MAX_CONCURRENT,
            desc=f"Discovery round {rnd}/{DISCOVERY_ROUNDS}",
        )
        round_cats = [c for res in results if res for c in res]
        all_cats.extend(round_cats)
        log.info(
            "Round %d: +%d cats  (running total unique: %d)",
            rnd,
            len(round_cats),
            len(set(all_cats)),
        )

    return sorted(set(all_cats))


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — PHASE 4: TAXONOMY CONSOLIDATION
# ══════════════════════════════════════════════════════════════════════════════

_MERGE_SYSTEM = (
    "You are an expert procurement taxonomy architect.\n"
    "You will receive two lists:\n"
    "  1. RAW CANDIDATES: category labels extracted from real procurement item descriptions.\n"
    "  2. REFERENCE LIST: conventional procurement category names (for guidance only).\n\n"
    "Your task is to produce a FINAL TAXONOMY as a JSON array of canonical category names.\n\n"
    "Requirements:\n"
    "- Merge synonyms and near-duplicates into ONE canonical category.\n"
    "  Example: ppe / safety equipment / personal protective equipment -> ppe\n"
    "- Explicitly normalize and merge ALL office- and paper-related concepts into the single\n"
    '  canonical category "stationery" (e.g., pen, pencil, notebook, paper, file, folder,\n'
    "  printer paper, office supplies → stationery).\n"
    "- All category names must be GENERIC noun phrases (1–5 words), lowercase.\n"
    '- Preserve meaningful distinctions (e.g., "electrical component" ≠ "electrical service").\n'
    "- Consolidate as many related raw candidates as possible into sensible canonical categories.\n"
    "- Aim to reduce and organize the full set of raw labels (which may be very large) into a\n"
    "  compact but expressive taxonomy with a total length of 100 to 150 categories.\n"
    '- Always include "other" as a catch-all category at the end.\n'
    "- Return ONLY the JSON array of category strings — no prose, no markdown, no comments.\n\n"
    "Example output:\n"
    '\'["fastener", "lubricant", "electrical component", "ppe", "stationery", "other"]\''
)


async def consolidate_taxonomy(
    raw_cats: list[str],
    cfg: ModelConfig,
    rate_limiter: RateLimiter,
) -> list[str]:
    """Single API call: merge raw candidates -> clean final taxonomy."""
    client = make_client(cfg)
    user_msg = (
        "RAW CANDIDATE CATEGORIES:\n"
        + json.dumps(sorted(raw_cats), ensure_ascii=False)
        + "\n\nREFERENCE LIST (guidance only):\n"
        + json.dumps(REFERENCE_CATEGORIES, ensure_ascii=False)
        + "\n\nReturn the merged taxonomy as a JSON array."
    )
    out_budget = 1024
    n_input = count_tokens(_MERGE_SYSTEM + user_msg, cfg.encoding)
    await rate_limiter.acquire(
        n_input + out_budget + reasoning_headroom(cfg, out_budget)
    )

    params = build_api_params(cfg, _MERGE_SYSTEM, user_msg, out_budget, temperature=0.0)

    async def _call():
        resp = await client.chat.completions.create(**params)
        return extract_content(resp, cfg, tag="consolidate")

    raw = await with_retry(_call, tag="consolidate", retries=MAX_RETRIES)
    cats = extract_string_list(raw)

    if len(cats) < 5:
        log.warning("Consolidation returned %d categories — falling back.", len(cats))
        cats = sorted({*raw_cats, *REFERENCE_CATEGORIES})

    if "other" not in cats:
        cats.append("other")

    return sorted({c.strip().lower() for c in cats if c and c.strip()})


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 10 — PHASE 5: FULL CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

_FALLBACK = {"category": "other", "core_product": "unclassified", "confidence": "low"}


def build_cls_system(categories: list[str]) -> str:
    cat_pipe = " | ".join(categories)
    return (
        "You are an expert procurement item classification engine.\n"
        "For EACH item in the provided JSON array, assign the most accurate and specific category.\n\n"
        f"ALLOWED CATEGORIES (select EXACTLY one per item):\n{cat_pipe}\n\n"
        "Output format:\n"
        "Return ONLY a JSON array — one object per input item, in the SAME ORDER.\n"
        "Each object must follow this schema:\n"
        '  {"category": "<allowed category>", '
        '"core_product": "<generic 2–4 word noun phrase>", '
        '"confidence": "<high|medium|low>"}\n\n'
        "Classification rules:\n"
        "1. Always choose the MOST SPECIFIC and BEST-FIT category from the allowed list.\n"
        "   Use 'other' ONLY if no category reasonably applies.\n"
        "2. Prefer assigning a close, semantically correct category over defaulting to 'other'.\n"
        "3. core_product must be GENERIC and normalized:\n"
        "   - Use noun phrases only (2–4 words).\n"
        "   - Do NOT include sizes, grades, brands, materials, or specifications.\n"
        "   - Normalize common synonyms (bolt/screw/nut → fastener; grease/oil → lubricant).\n"
        "4. If the item clearly represents a service (e.g., repair, testing, painting, labor),\n"
        "   select the most appropriate SERVICE category from the allowed list.\n"
        "5. confidence levels:\n"
        "   - high: category is explicit and unambiguous\n"
        "   - medium: category is inferred but reasonable\n"
        "   - low: weak signal, partial match, or ambiguous description\n"
        "6. Do NOT invent new categories or modify the allowed category names.\n"
        "7. Return ONLY the JSON array — no explanations, no markdown, no additional keys."
    )


def load_cache() -> dict:
    if Path(CACHE_FILE).exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            log.info("Cache loaded: %s items.", f"{len(data):,}")
            return data
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cache unreadable (%s) — starting fresh.", exc)
    return {}


def save_cache(cache: dict) -> None:
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


async def classify_batch(
    client: AsyncOpenAI,
    cfg: ModelConfig,
    rate_limiter: RateLimiter,
    item_registry: dict,
    cls_system: str,
    valid_cats: set[str],
    batch: list[str],
    idx: int,
    cache: dict,
) -> dict:
    """
    Classify one batch. Returns {canonical_item -> classification_record}.

    Failure handling:
    - API failure after all retries     -> fallback record for every item
    - Response shorter than batch       -> pad missing items with fallback
    - Response longer than batch        -> silently truncate
    - Invalid category string           -> substring-match against valid_cats, else 'other'
    - Invalid confidence string         -> coerce to 'low'
    """
    to_classify = [i for i in batch if i not in cache]
    if not to_classify:
        return {}

    cleaned = [item_registry[i]["cleaned"] for i in to_classify]
    user_content = json.dumps(cleaned, ensure_ascii=False)
    n_input_tok = count_tokens(cls_system + user_content, cfg.encoding)

    # For reasoning models we must reserve the full max_out_tokens for TPM
    # because we cannot know how many tokens reasoning will consume.
    out_budget = (
        cfg.max_out_tokens
        if cfg.is_reasoning
        else min(cfg.max_out_tokens, len(to_classify) * 40)
    )
    await rate_limiter.acquire(
        n_input_tok + out_budget + reasoning_headroom(cfg, out_budget)
    )

    params = build_api_params(
        cfg, cls_system, user_content, out_budget, temperature=0.05
    )

    async def _call():
        resp = await client.chat.completions.create(**params)
        return extract_content(resp, cfg, tag=f"cls-{idx}")

    raw_resp = await with_retry(_call, tag=f"cls-{idx}")
    records = extract_classification_list(raw_resp, expected=len(to_classify))
    result: dict = {}

    if records:
        if len(records) < len(to_classify):
            records += [_FALLBACK.copy()] * (len(to_classify) - len(records))
        records = records[: len(to_classify)]

        for item, rec in zip(to_classify, records):
            if not isinstance(rec, dict):
                rec = _FALLBACK.copy()

            cat = str(rec.get("category", "")).strip().lower()
            if cat not in valid_cats:
                close = [c for c in valid_cats if cat in c or c in cat]
                cat = min(close, key=len) if close else "other"

            conf = str(rec.get("confidence", "low")).strip().lower()
            if conf not in {"high", "medium", "low"}:
                conf = "low"

            result[item] = {
                "category": cat,
                "core_product": str(rec.get("core_product", "")).strip()
                or "unclassified",
                "confidence": conf,
            }
    else:
        log.warning(
            "[cls-%d] Complete failure — fallback for %d items.", idx, len(to_classify)
        )
        for item in to_classify:
            result[item] = _FALLBACK.copy()

    # Deterministic post-classification overrides (no API cost)
    for item, rec in result.items():
        flags = item_registry.get(item, {}).get("flags", {})
        if flags.get("tax_noise") and rec["category"] == "other":
            rec["category"] = "tax and accounting"
            rec["core_product"] = "tax charge"
        if flags.get("too_short") and rec["confidence"] == "high":
            rec["confidence"] = "medium"

    return result


async def classify_all(
    item_registry: dict,
    cfg: ModelConfig,
    rate_limiter: RateLimiter,
    final_categories: list[str],
) -> dict:
    """Classify all unique items; checkpoint to disk every CHECKPOINT_EVERY batches."""
    cache = load_cache()
    unique_items = list(item_registry.keys())
    remaining = [i for i in unique_items if i not in cache]

    log.info(
        "Classification: total=%s  cached=%s  remaining=%s",
        f"{len(unique_items):,}",
        f"{len(cache):,}",
        f"{len(remaining):,}",
    )

    if not remaining:
        log.info("All items cached — skipping API calls.")
        return cache

    cls_system = build_cls_system(final_categories)
    valid_cats = set(final_categories) | {"tax and accounting"}
    client = make_client(cfg)
    batches = [
        remaining[i : i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)
    ]

    for chunk_start in range(0, len(batches), CHECKPOINT_EVERY):
        chunk = batches[chunk_start : chunk_start + CHECKPOINT_EVERY]
        coros = [
            classify_batch(
                client,
                cfg,
                rate_limiter,
                item_registry,
                cls_system,
                valid_cats,
                b,
                chunk_start + i,
                cache,
            )
            for i, b in enumerate(chunk)
        ]
        results = await run_parallel(
            coros,
            max_concurrent=MAX_CONCURRENT,
            desc=(
                f"Classifying batches "
                f"{chunk_start+1}-{min(chunk_start+len(chunk), len(batches))}"
                f"/{len(batches)}"
            ),
        )
        for res in results:
            if isinstance(res, dict):
                cache.update(res)

        save_cache(cache)
        log.info(
            "Checkpoint: %s/%s classified (%.1f%%)",
            f"{len(cache):,}",
            f"{len(unique_items):,}",
            len(cache) / len(unique_items) * 100,
        )

    save_cache(cache)
    log.info("Classification complete: %s items.", f"{len(cache):,}")
    return cache


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 11 — PHASE 6: EXCEL WRITE-BACK
# ══════════════════════════════════════════════════════════════════════════════

S = {
    "dark_blue": "1F4E79",
    "mid_blue": "2E75B6",
    "dark_green": "375623",
    "mid_green": "548235",
    "orange": "C55A11",
    "red": "C00000",
    "lt_green": "E2EFDA",
    "lt_yellow": "FFF2CC",
    "lt_grey": "F2F2F2",
    "white": "FFFFFF",
    "grey": "595959",
}
CONF_COLOR = {
    "high": S["mid_green"],
    "medium": S["orange"],
    "low": S["red"],
    "unclassified": S["grey"],
}
_THIN = Side(style="thin", color="BFBFBF")
STD_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
NEW_COLS = [
    ("Item Category", "category"),
    ("Core Product", "core_product"),
    ("Classification Confidence", "confidence"),
]


def _fill(h: str) -> PatternFill:
    return PatternFill("solid", start_color=h)


def _font(h: str, bold: bool = False, sz: int = 9) -> Font:
    return Font(bold=bold, color=h, name="Arial", size=sz)


def _style_hdr(cell, fill_h: str) -> None:
    cell.fill = _fill(fill_h)
    cell.font = _font(S["white"], bold=True, sz=10)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = STD_BORDER


def _style_data(cell, fill_h: str, font_h: str = "000000", bold: bool = False) -> None:
    cell.fill = _fill(fill_h)
    cell.font = _font(font_h, bold=bold)
    cell.alignment = Alignment(vertical="center", wrap_text=False)
    cell.border = STD_BORDER


def resolve_field(
    raw_item: Any, field: str, item_registry: dict, cache: dict
) -> Optional[str]:
    """Look up a classification field for any raw item string."""
    s = str(raw_item).strip() if raw_item is not None else ""
    if not s or s.lower() in _SKIP_VALUES:
        return None
    if s in cache:
        return cache[s].get(field)
    lower = item_registry.get(s, {}).get("lower", s.lower())
    for k, v in item_registry.items():
        if v.get("lower") == lower and k in cache:
            return cache[k].get(field)
    return None


def write_excel(
    df_raw: pd.DataFrame,
    item_registry: dict,
    cache: dict,
    final_categories: list[str],
    cfg: ModelConfig,
) -> pd.DataFrame:
    """Apply classifications to df_raw and write the formatted Excel output."""
    log.info("Mapping classifications onto %s rows ...", f"{len(df_raw):,}")
    df_out = df_raw.copy()

    for col_label, cache_field in NEW_COLS:
        df_out[col_label] = [
            resolve_field(v, cache_field, item_registry, cache)
            for v in tqdm(df_out[ITEM_COLUMN], desc=f"  {col_label}", leave=False)
        ]
        df_out[col_label] = df_out[col_label].fillna("unclassified")

    covered = (df_out["Item Category"] != "unclassified").sum()
    log.info(
        "Coverage: %s/%s rows (%.1f%%)",
        f"{covered:,}",
        f"{len(df_out):,}",
        covered / len(df_out) * 100,
    )

    log.info("Writing %s ...", OUTPUT_FILE)
    df_out.to_excel(OUTPUT_FILE, sheet_name=INPUT_SHEET, index=False, engine="openpyxl")

    wb = load_workbook(OUTPUT_FILE)
    ws = wb[INPUT_SHEET]
    hdr = [c.value for c in ws[1]]

    new_idxs = {lbl: (hdr.index(lbl) + 1) for lbl, _ in NEW_COLS if lbl in hdr}
    item_idx = (hdr.index(ITEM_COLUMN) + 1) if ITEM_COLUMN in hdr else None
    conf_idx = new_idxs.get("Classification Confidence")

    # Header row
    for ci, col_name in enumerate(hdr, 1):
        _style_hdr(
            ws.cell(row=1, column=ci),
            S["dark_green"] if col_name in new_idxs else S["dark_blue"],
        )
    ws.row_dimensions[1].height = 30

    # Data rows
    for ri in tqdm(range(2, ws.max_row + 1), desc="Styling rows", leave=False):
        zebra = S["lt_grey"] if ri % 2 == 0 else S["white"]
        conf_val = ws.cell(row=ri, column=conf_idx).value if conf_idx else "low"
        conf_col = CONF_COLOR.get(str(conf_val).lower(), S["grey"])
        for lbl, ci in new_idxs.items():
            cell = ws.cell(row=ri, column=ci)
            if lbl == "Item Category":
                _style_data(cell, S["lt_green"], S["dark_green"])
            elif lbl == "Classification Confidence":
                _style_data(cell, S["lt_yellow"], conf_col)
            else:
                _style_data(cell, zebra)

    # Column widths
    for ci, col_name in enumerate(hdr, 1):
        ltr = get_column_letter(ci)
        ws.column_dimensions[ltr].width = (
            52 if ci == item_idx else 28 if col_name in new_idxs else 20
        )

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # ── Category Summary sheet ────────────────────────────────────────────────
    ws_s = wb.create_sheet("Category Summary", 0)
    ws_s.sheet_view.showGridLines = False

    ws_s.merge_cells("B2:J2")
    tc = ws_s["B2"]
    tc.value = "ITEM CATEGORY CLUSTERING — SUMMARY"
    tc.font = _font(S["white"], bold=True, sz=16)
    tc.fill = _fill(S["dark_blue"])
    tc.alignment = Alignment(horizontal="center", vertical="center")
    ws_s.row_dimensions[2].height = 36

    ws_s.merge_cells("B3:J3")
    mc = ws_s["B3"]
    mc.value = (
        f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')}  |  "
        f"Input: {INPUT_FILE}  |  Rows: {len(df_out):,}  |  "
        f"Unique items: {len(item_registry):,}  |  "
        f"Model: {cfg.model_id}  |  Categories: {len(final_categories)}"
    )
    mc.font = _font(S["white"], sz=9)
    mc.fill = _fill(S["mid_blue"])
    mc.alignment = Alignment(horizontal="center", vertical="center")
    ws_s.row_dimensions[3].height = 18

    for ci, hdr_txt in enumerate(
        [
            "#",
            "Category",
            "Unique Items",
            "Total Rows",
            "Row %",
            "Avg Confidence",
            "High",
            "Medium",
            "Low",
        ],
        2,
    ):
        _style_hdr(ws_s.cell(row=5, column=ci, value=hdr_txt), S["dark_blue"])
    ws_s.row_dimensions[5].height = 25

    u_per_cat = Counter(v["category"] for v in cache.values())
    cscore = {"high": 3, "medium": 2, "low": 1, "unclassified": 0}
    cat_rows: dict = defaultdict(lambda: {"rows": 0, "confs": []})
    for cv, cfv in zip(df_out["Item Category"], df_out["Classification Confidence"]):
        cat_rows[cv]["rows"] += 1
        cat_rows[cv]["confs"].append(str(cfv).lower())

    for ri, cat in enumerate(sorted(cat_rows, key=lambda c: -cat_rows[c]["rows"]), 6):
        st = cat_rows[cat]
        confs = st["confs"]
        avg_c = (
            round(sum(cscore.get(c, 1) for c in confs) / len(confs), 2) if confs else 0
        )
        zebra = S["lt_grey"] if ri % 2 == 0 else S["white"]
        row_d = [
            ri - 5,
            cat,
            u_per_cat.get(cat, 0),
            st["rows"],
            round(st["rows"] / len(df_out) * 100, 2),
            avg_c,
            confs.count("high"),
            confs.count("medium"),
            confs.count("low"),
        ]
        for ci, val in enumerate(row_d, 2):
            cell = ws_s.cell(row=ri, column=ci, value=val)
            if ci == 3:
                _style_data(cell, S["lt_green"], S["dark_green"], bold=True)
            else:
                _style_data(cell, zebra)
        ws_s.row_dimensions[ri].height = 18

    for ci, w in enumerate([3, 3, 38, 16, 14, 9, 20, 8, 10, 10], 1):
        ws_s.column_dimensions[get_column_letter(ci)].width = w
    ws_s.freeze_panes = "C6"

    wb.save(OUTPUT_FILE)
    log.info("Saved -> %s", OUTPUT_FILE)
    return df_out


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 12 — MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


async def _run_pipeline() -> None:
    # ── Validate config ───────────────────────────────────────────────────────
    assert (
        ACTIVE_MODEL in MODEL_REGISTRY
    ), f"'{ACTIVE_MODEL}' not in MODEL_REGISTRY. Available: {list(MODEL_REGISTRY)}"
    cfg = MODEL_REGISTRY[ACTIVE_MODEL]

    key = OPENAI_API_KEY.strip()
    if not key or key.startswith("sk-..."):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Export it or edit the Configuration section."
        )

    log.info("=" * 66)
    log.info("  PROCUREMENT CLUSTERING — START")
    log.info("=" * 66)
    log.info("  Model         : %s", cfg.model_id)
    log.info("  Is reasoning  : %s", cfg.is_reasoning)
    log.info("  System role   : %s", cfg.system_role)
    log.info(
        "  Token param   : %s",
        "max_completion_tokens" if cfg.use_completion_tokens else "max_tokens",
    )
    log.info(
        "  Temperature   : %s",
        "omitted" if not cfg.supports_temperature else "included",
    )
    log.info("  TPM (eff.)    : %s", f"{int(cfg.tpm_limit * RATE_LIMIT_MARGIN):,}")
    log.info("  RPM (eff.)    : %s", f"{int(cfg.rpm_limit * RATE_LIMIT_MARGIN):,}")
    log.info("  Input file    : %s", INPUT_FILE)

    rate_limiter = RateLimiter(cfg.rpm_limit, cfg.tpm_limit)

    # ── Phase 1: Load ─────────────────────────────────────────────────────────
    log.info("[1/7] Loading %s ...", INPUT_FILE)
    df_raw = pd.read_excel(INPUT_FILE, sheet_name=INPUT_SHEET, dtype=str)
    df_raw = df_raw.where(pd.notna(df_raw), None)
    if ITEM_COLUMN not in df_raw.columns:
        raise ValueError(
            f"Column '{ITEM_COLUMN}' not found. Available: {df_raw.columns.tolist()}"
        )
    item_series = df_raw[ITEM_COLUMN].fillna("").astype(str).str.strip()
    log.info("Loaded: %s rows x %d columns", f"{len(df_raw):,}", df_raw.shape[1])

    if "Commodity Group" in df_raw.columns:
        log.info(
            "Commodity Group distribution:\n%s",
            df_raw["Commodity Group"].value_counts().to_string(),
        )

    # ── Phase 2: Preprocess ───────────────────────────────────────────────────
    log.info("[2/7] Preprocessing items ...")
    item_registry = build_item_registry(item_series, cfg)
    unique_items = list(item_registry.keys())
    total_tokens = sum(r["tokens"] for r in item_registry.values())
    log.info(
        "Unique items: %s  |  Total tokens: %s",
        f"{len(unique_items):,}",
        f"{total_tokens:,}",
    )
    log.info(
        "Estimated batches: %s x %d items",
        f"{math.ceil(len(unique_items)/BATCH_SIZE):,}",
        BATCH_SIZE,
    )

    # ── Phase 3: Category discovery ───────────────────────────────────────────
    log.info("[3/7] Running category discovery (%d rounds) ...", DISCOVERY_ROUNDS)
    raw_candidates = await run_discovery(df_raw, item_registry, cfg, rate_limiter)
    log.info("Raw candidates: %d", len(raw_candidates))
    for c in raw_candidates:
        log.info("  * %s", c)

    # ── Phase 4: Taxonomy consolidation ──────────────────────────────────────
    log.info("[4/7] Consolidating taxonomy ...")
    final_categories = await consolidate_taxonomy(raw_candidates, cfg, rate_limiter)
    with open(TAXONOMY_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "categories": final_categories,
                "count": len(final_categories),
                "model": cfg.model_id,
                "generated_at": datetime.now().isoformat(),
            },
            fh,
            indent=2,
        )
    log.info(
        "Final taxonomy (%d categories) saved -> %s",
        len(final_categories),
        TAXONOMY_FILE,
    )
    for i, cat in enumerate(final_categories, 1):
        log.info("  %3d. %s", i, cat)

    # ── Phase 5: Full classification ──────────────────────────────────────────
    log.info("[5/7] Classifying all items ...")
    cache = await classify_all(item_registry, cfg, rate_limiter, final_categories)

    cat_counts = Counter(v["category"] for v in cache.values())
    conf_counts = Counter(v["confidence"] for v in cache.values())
    log.info("Category distribution (unique items):")
    total_cached = sum(cat_counts.values())
    for cat, cnt in cat_counts.most_common():
        log.info("  %-35s %6d  %.1f%%", cat, cnt, cnt / total_cached * 100)
    log.info("Confidence: %s", dict(conf_counts))

    # ── Phase 6: Write Excel ──────────────────────────────────────────────────
    log.info("[6/7] Writing Excel output ...")
    df_out = write_excel(df_raw, item_registry, cache, final_categories, cfg)

    # ── Phase 7: Validation summary ───────────────────────────────────────────
    log.info("[7/7] Validation summary")
    total = len(df_out)
    cls = (df_out["Item Category"] != "unclassified").sum()
    uncls = total - cls
    high = (df_out["Classification Confidence"] == "high").sum()
    med = (df_out["Classification Confidence"] == "medium").sum()
    low = (df_out["Classification Confidence"] == "low").sum()

    print("\n" + "=" * 66)
    print("  PROCUREMENT CLUSTERING — COMPLETE")
    print("=" * 66)
    print(f"  Output         : {OUTPUT_FILE}")
    print(f"  Cache          : {CACHE_FILE}")
    print(f"  Taxonomy       : {TAXONOMY_FILE}")
    print(f"  Model          : {cfg.model_id}")
    print(f"  Categories     : {len(final_categories)}")
    print(f"\n  Coverage")
    print(f"    Total rows   : {total:>10,}")
    print(f"    Classified   : {cls:>10,}  ({cls/total*100:.1f}%)")
    print(f"    Unclassified : {uncls:>10,}  ({uncls/total*100:.1f}%)")
    print(f"\n  Confidence")
    print(f"    High         : {high:>10,}  ({high/total*100:.1f}%)")
    print(f"    Medium       : {med:>10,}  ({med/total*100:.1f}%)")
    print(f"    Low          : {low:>10,}  ({low/total*100:.1f}%)")
    print(f"\n  Final Categories ({len(final_categories)}):")

    cat_row_counts = defaultdict(int)
    for cv in df_out["Item Category"]:
        cat_row_counts[cv] += 1
    for i, cat in enumerate(final_categories, 1):
        cnt = cat_row_counts.get(cat, 0)
        print(f"    {i:>3}. {cat:<36} {cnt:>7,}  ({cnt/total*100:.1f}%)")

    review = (
        df_out[
            (df_out["Classification Confidence"] == "low")
            & (df_out["Item Category"] != "unclassified")
        ][[ITEM_COLUMN, "Item Category", "Core Product"]]
        .drop_duplicates(ITEM_COLUMN)
        .head(20)
    )
    if len(review):
        print(f"\n  Low-confidence items for review (top {len(review)}):")
        for _, r in review.iterrows():
            print(f"    [{r['Item Category']:<26}]  {str(r[ITEM_COLUMN])[:55]}")

    print("\n" + "=" * 66)
    print(f"  Open {OUTPUT_FILE} to explore results.")
    print("=" * 66)


def main() -> None:
    asyncio.run(_run_pipeline())


if __name__ == "__main__":
    main()
