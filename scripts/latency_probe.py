#!/usr/bin/env python3
"""Measure streaming latency and throughput for an OpenAI-compatible gateway.

The probe is intentionally dependency-free. It reports:
  - first reasoning token
  - time to first visible output token (TTFT)
  - time to last visible token / request completion
  - body tokens per second (output tokens minus reasoning tokens)
  - cached prompt tokens and reasoning-token share
"""

import argparse
import base64
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def parse_event(protocol, obj):
    """Translate one SSE JSON object into a small, protocol-neutral event."""
    if not isinstance(obj, dict):
        return None
    if protocol == "responses":
        kind = obj.get("type")
        if kind == "response.reasoning_summary_text.delta":
            return {"kind": "reasoning", "text": obj.get("delta") or ""}
        if kind == "response.output_text.delta":
            return {"kind": "text", "text": obj.get("delta") or ""}
        if kind in ("response.completed", "response.incomplete", "response.failed"):
            response = obj.get("response") or {}
            return {
                "kind": "completed",
                "status": response.get("status") or kind,
                "usage": response.get("usage") or {},
                "error": response.get("error"),
            }
        return None

    usage = obj.get("usage")
    if usage:
        return {"kind": "usage", "usage": usage}
    choices = obj.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    if delta.get("reasoning_content"):
        return {"kind": "reasoning", "text": delta.get("reasoning_content") or ""}
    if delta.get("content"):
        return {"kind": "text", "text": delta.get("content") or ""}
    return None


def summarize(events, started_at, finished_at):
    """Build timing metrics from (timestamp, event) pairs."""
    first_reasoning = None
    first_text = None
    last_text = None
    usage = {}
    status = "completed"
    error = None
    text = ""
    for timestamp, event in events:
        if not event:
            continue
        kind = event.get("kind")
        if kind == "reasoning" and event.get("text") and first_reasoning is None:
            first_reasoning = timestamp
        elif kind == "text" and event.get("text"):
            if first_text is None:
                first_text = timestamp
            last_text = timestamp
            text += event["text"]
        elif kind in ("usage", "completed"):
            usage = event.get("usage") or usage
            status = event.get("status") or status
            error = event.get("error") or error

    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    reasoning_tokens = (
        (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        or 0
    )
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    cached_tokens = (
        (usage.get("input_tokens_details") or {}).get("cached_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        or 0
    )
    body_tokens = output_tokens
    if output_tokens is not None:
        body_tokens = max(0, output_tokens - reasoning_tokens)
    generation_seconds = None
    body_tps = None
    if first_text is not None and last_text is not None and last_text > first_text:
        generation_seconds = last_text - first_text
        if body_tokens is not None:
            body_tps = body_tokens / generation_seconds
    return {
        "status": status,
        "error": error,
        "first_reasoning_ms": (
            (first_reasoning - started_at) * 1000 if first_reasoning else None
        ),
        "ttft_ms": (first_text - started_at) * 1000 if first_text else None,
        "total_ms": (finished_at - started_at) * 1000,
        "generation_ms": generation_seconds * 1000 if generation_seconds else None,
        "body_tps": body_tps,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "body_tokens": body_tokens,
        "text": text,
    }


def _load_key(repo, key_env):
    if key_env in os.environ:
        return os.environ[key_env]
    env_path = Path(repo).expanduser().resolve() / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key_env + "="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("missing API key: set %s or pass --repo" % key_env)


def _context_text(chars):
    if chars <= 0:
        return ""
    unit = (
        "Latency is affected by queueing, prompt prefill, cache hits, network "
        "round trips, and token generation speed. "
    )
    return (unit * ((chars // len(unit)) + 1))[:chars]


def _request_body(protocol, model, prompt, max_tokens, reasoning, nonce, image_url=None):
    if protocol == "responses":
        content = [{"type": "input_text", "text": prompt}]
        if image_url:
            content.append({"type": "input_image", "image_url": image_url})
        payload = {
            "model": model,
            "stream": True,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_tokens,
            "prompt_cache_key": "latency-probe-" + nonce,
        }
        if reasoning:
            payload["reasoning"] = {"effort": reasoning}
        return payload
    content = [{"type": "text", "text": prompt}]
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    payload = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    return payload


def _image_data_url(path):
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(path).suffix.lower())
    if not media_type:
        raise SystemExit("unsupported image type: %s" % path)
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (media_type, data)


def measure(url, api_key, body, timeout, extra_headers=None):
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )
    started_at = time.perf_counter()
    events = []
    protocol = "responses" if url.rstrip("/").endswith("/responses") else "chat"
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            event = parse_event(protocol, obj)
            if event:
                events.append((time.perf_counter(), event))
    finished_at = time.perf_counter()
    summary = summarize(events, started_at, finished_at)
    summary["request_bytes"] = len(payload)
    return summary


def _percentile(values, fraction):
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return values[index]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--protocol", choices=("responses", "chat"), default="responses")
    parser.add_argument("--api-key-env", default="MASTER_API_KEY")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--reasoning", choices=("none", "low", "high", "max"), default="high")
    parser.add_argument("--context-chars", type=int, default=0)
    parser.add_argument("--cache-mode", choices=("warm", "cold"), default="warm")
    parser.add_argument("--image", help="attach one image as a data URI")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="extra request header, repeatable",
    )
    parser.add_argument("--prompt", default="Write one short paragraph of about 120 words about network latency.")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples must be >= 1")

    api_key = _load_key(args.repo, args.api_key_env)
    base = args.base_url.rstrip("/")
    url = base + ("/responses" if args.protocol == "responses" else "/chat/completions")
    context = _context_text(args.context_chars)
    image_url = _image_data_url(args.image) if args.image else None
    extra_headers = {}
    for item in args.header:
        name, separator, value = item.partition("=")
        if not separator or not name:
            parser.error("--header must be NAME=VALUE")
        extra_headers[name] = value

    def body_for(index):
        nonce = uuid.uuid4().hex if args.cache_mode == "cold" else "warm"
        prompt = context + "\n\n" + args.prompt
        if args.cache_mode == "cold":
            prompt = "Session " + nonce + "\n" + prompt
        return _request_body(
            args.protocol,
            args.model,
            prompt,
            args.max_tokens,
            args.reasoning,
            nonce,
            image_url=image_url,
        )

    for index in range(args.warmup if args.cache_mode == "warm" else 0):
        measure(url, api_key, body_for(-1), args.timeout, extra_headers)

    samples = []
    for index in range(args.samples):
        try:
            samples.append(
                measure(url, api_key, body_for(index), args.timeout, extra_headers)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            samples.append(
                {
                    "status": "error",
                    "error": str(exc),
                    "ttft_ms": None,
                    "total_ms": None,
                    "body_tps": None,
                }
            )

    summary = {
        "url": url,
        "model": args.model,
        "reasoning": args.reasoning,
        "cache_mode": args.cache_mode,
        "context_chars": args.context_chars,
        "image": args.image,
        "samples": len(samples),
        "ttft_ms_p50": _percentile([s.get("ttft_ms") for s in samples], 0.5),
        "ttft_ms_p95": _percentile([s.get("ttft_ms") for s in samples], 0.95),
        "body_tps_p50": _percentile([s.get("body_tps") for s in samples], 0.5),
        "total_ms_p50": _percentile([s.get("total_ms") for s in samples], 0.5),
        "request_bytes_p50": _percentile(
            [s.get("request_bytes") for s in samples], 0.5
        ),
        "errors": sum(1 for s in samples if s.get("status") == "error"),
    }
    if args.json:
        print(json.dumps({"summary": summary, "runs": samples}, ensure_ascii=False, indent=2))
        return 0 if summary["errors"] == 0 else 1

    print(
        "%s model=%s reasoning=%s cache=%s context=%d chars"
        % (url, args.model, args.reasoning, args.cache_mode, args.context_chars)
    )
    print(
        "runs=%d errors=%d ttft_p50=%sms ttft_p95=%sms body_tps_p50=%s total_p50=%sms bytes_p50=%s"
        % (
            summary["samples"],
            summary["errors"],
            _fmt(summary["ttft_ms_p50"]),
            _fmt(summary["ttft_ms_p95"]),
            _fmt(summary["body_tps_p50"]),
            _fmt(summary["total_ms_p50"]),
            _fmt(summary["request_bytes_p50"], 0),
        )
    )
    for index, sample in enumerate(samples, 1):
        print(
            "  #%d status=%s reason=%sms ttft=%sms total=%sms tps=%s bytes=%s cached=%s/%s out=%s rsn=%s"
            % (
                index,
                sample.get("status"),
                _fmt(sample.get("first_reasoning_ms")),
                _fmt(sample.get("ttft_ms")),
                _fmt(sample.get("total_ms")),
                _fmt(sample.get("body_tps")),
                sample.get("request_bytes", "-"),
                sample.get("cached_tokens", "-"),
                sample.get("input_tokens", "-"),
                sample.get("output_tokens", "-"),
                sample.get("reasoning_tokens", "-"),
            )
        )
    return 0 if summary["errors"] == 0 else 1


def _fmt(value, digits=1):
    return "-" if value is None else ("%." + str(digits) + "f") % value


if __name__ == "__main__":
    sys.exit(main())
