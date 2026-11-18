"""Model access: discovery, small-model filter, timeouts, fallback, selftest.

Only code in this module talks to a model. Rules enforced here:
  * discovery is not a model call (no tokens, no number, no area tag);
  * a model outside the small-model chain can never be called;
  * every wait is cancelled by a thread timeout (a hung call cannot block a run);
  * failed attempts that returned no usage are [CODE] lines, never numbered calls;
  * an attempt that returned a response (even unusable) is a numbered call with
    the provider's own model name and token counts.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import store

DISCOVERY_TIMEOUT = 5.0
CALL_TIMEOUT = 25.0
CACHE_TTL = 600.0
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
NOT_TEXT_GEN = ("embed", "rerank", "whisper", "tts", "imagen", "veo", "aqa")


# ---------------------------------------------------------------- errors
class LLMError(Exception):
    def __init__(self, reason: str, transient: bool = False, timeout: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient
        self.timeout = timeout


class NoSmallModel(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


class Deadline:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())

    def elapsed(self) -> float:
        return self.seconds - self.remaining()


# ---------------------------------------------------------------- config
def _csv(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def load_env(path: Path | None = None) -> None:
    """Load .env without overriding real environment variables."""
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    provider: str = "gemini"
    gemini_key: str = ""
    base_url: str = ""
    api_key: str = "none"
    primary: str = ""
    fallback: str = ""
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    exact: list = field(default_factory=list)
    thinking_budget: int | None = None

    def key(self) -> str:
        return json.dumps([self.provider, self.base_url, self.primary, self.fallback,
                           self.include, self.exclude, self.exact], sort_keys=True)


def load_config() -> Config:
    e = os.environ
    provider = (e.get("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "openai_compatible" if e.get("OPENAI_BASE_URL") else "gemini"
    tb = e.get("GEMINI_THINKING_BUDGET", "").strip()
    return Config(
        provider=provider,
        gemini_key=e.get("GEMINI_API_KEY", ""),
        base_url=(e.get("OPENAI_BASE_URL") or "").strip(),
        api_key=e.get("OPENAI_API_KEY") or "none",
        primary=(e.get("MODEL_PRIMARY") or "").strip(),
        fallback=(e.get("MODEL_FALLBACK") or "").strip(),
        include=_csv(e.get("SMALL_MODEL_INCLUDE")),
        exclude=_csv(e.get("SMALL_MODEL_EXCLUDE")),
        exact=_csv(e.get("SMALL_MODEL_EXACT")),
        thinking_budget=int(tb) if tb.lstrip("-").isdigit() else None,
    )


def norm_name(n: str) -> str:
    return n[len("models/"):] if n.startswith("models/") else n


def is_local(cfg: Config) -> bool:
    if cfg.provider != "openai_compatible" or not cfg.base_url:
        return False
    return (urlparse(cfg.base_url).hostname or "").lower() in LOCAL_HOSTS


# ---------------------------------------------------------------- filter
def small_rule(name: str, cfg: Config) -> tuple[bool, str]:
    """Return (is_small, rule). EXCLUDE always wins, case-insensitive."""
    n = name.lower()
    for ex in cfg.exclude:
        if ex.lower() in n:
            return False, f"EXCLUDE '{ex}'"
    for t in NOT_TEXT_GEN:
        if t in n:
            return False, f"not a text-generation model ('{t}')"
    if n in [x.lower() for x in cfg.exact]:
        return True, "SMALL_MODEL_EXACT"
    for inc in cfg.include:
        if inc.lower() in n:
            return True, f"INCLUDE '{inc}'"
    if is_local(cfg):
        return True, "served from localhost (runs on this laptop)"
    return False, "not on SMALL_MODEL_EXACT / INCLUDE lists"


@dataclass
class Chain:
    models: list = field(default_factory=list)
    discovered: list = field(default_factory=list)
    rules: dict = field(default_factory=dict)       # kept model -> rule
    dropped: list = field(default_factory=list)     # (name, rule)
    source: str = "discovery"
    log: list = field(default_factory=list)

    def to_json(self):
        return {"models": self.models, "discovered": self.discovered, "rules": self.rules,
                "dropped": self.dropped, "source": self.source}


def run_with_timeout(fn, timeout: float):
    """Run fn in a daemon thread; abandon (really stop waiting) after timeout."""
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"timed out after {timeout:.1f}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


# ---------------------------------------------------------------- backends
@dataclass
class Raw:
    text: str
    model: str | None
    usage: dict


class GeminiBackend:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client_obj = None
        self._caps: dict = {}

    def _client(self):
        if not self.cfg.gemini_key:
            raise LLMError("GEMINI_API_KEY is not set")
        if self._client_obj is None:
            try:
                from google import genai  # type: ignore
            except ImportError:
                raise LLMError("google-genai SDK not installed (pip install google-genai)")
            self._client_obj = genai.Client(api_key=self.cfg.gemini_key)
        return self._client_obj

    @staticmethod
    def _wrap(e: Exception) -> LLMError:
        if isinstance(e, LLMError):
            return e
        msg = str(e)
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        low = msg.lower()
        transient = code in (429, 500, 502, 503, 504) or any(
            w in low for w in ("unavailable", "overloaded", "resource_exhausted", "rate limit", "quota"))
        return LLMError(f"{type(e).__name__}: {msg[:180]}", transient=transient)

    def list_models(self, timeout: float) -> list[str]:
        client = self._client()

        def go():
            out = []
            for m in client.models.list():
                acts = getattr(m, "supported_actions", None)
                if acts is not None and "generateContent" not in acts:
                    continue
                out.append(norm_name(m.name))
            return out
        return run_with_timeout(go, timeout)

    def generate(self, model: str, system: str, user: str, max_tokens: int,
                 timeout: float, json_mode: bool = True) -> Raw:
        client = self._client()
        try:
            from google.genai import types  # type: ignore
        except ImportError:
            raise LLMError("google-genai SDK not installed (pip install google-genai)")
        caps = self._caps.setdefault(model, {"system": True, "json": json_mode})
        resp = None
        for _ in range(3):  # capability fallbacks (no system prompt / no JSON mode)
            kw = dict(temperature=0, max_output_tokens=max_tokens)
            contents = user
            if caps["system"]:
                kw["system_instruction"] = system
            else:
                contents = system + "\n\n" + user
            if caps["json"] and json_mode:
                kw["response_mime_type"] = "application/json"
            if self.cfg.thinking_budget is not None:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=self.cfg.thinking_budget)
            try:
                resp = client.models.generate_content(
                    model=model, contents=contents, config=types.GenerateContentConfig(**kw))
                break
            except Exception as e:  # noqa: BLE001
                low = str(e).lower()
                if caps["system"] and ("system instruction" in low or "developer instruction" in low):
                    caps["system"] = False
                    continue
                if caps["json"] and ("json mode" in low or "mime" in low):
                    caps["json"] = False
                    continue
                raise self._wrap(e)
        if resp is None:
            raise LLMError("no response after capability fallbacks")
        try:
            text = resp.text or ""
        except Exception:  # noqa: BLE001  (blocked / no candidates)
            text = ""
        um = getattr(resp, "usage_metadata", None)
        usage = {
            "input": getattr(um, "prompt_token_count", None),
            "output": getattr(um, "candidates_token_count", None),
            "total": getattr(um, "total_token_count", None),
            "thinking": getattr(um, "thoughts_token_count", None),
        }
        return Raw(text, getattr(resp, "model_version", None), usage)


class OpenAIBackend:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _base(self) -> str:
        if not self.cfg.base_url:
            raise LLMError("OPENAI_BASE_URL is not set")
        return self.cfg.base_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json"}

    def list_models(self, timeout: float) -> list[str]:
        import requests
        r = requests.get(self._base() + "/models", headers=self._headers(), timeout=(timeout, timeout))
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        return [norm_name(i["id"] if isinstance(i, dict) else str(i)) for i in items]

    def generate(self, model: str, system: str, user: str, max_tokens: int,
                 timeout: float, json_mode: bool = True) -> Raw:
        import requests
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        use_json = json_mode
        for _ in range(2):
            if use_json:
                body["response_format"] = {"type": "json_object"}
            else:
                body.pop("response_format", None)
            try:
                r = requests.post(self._base() + "/chat/completions", headers=self._headers(),
                                  json=body, timeout=(5, max(1.0, timeout)))
            except requests.exceptions.Timeout:
                raise LLMError(f"timeout after {timeout:.0f}s", transient=True, timeout=True)
            except requests.exceptions.RequestException as e:
                raise LLMError(f"{type(e).__name__}: {str(e)[:160]}", transient=True)
            if r.status_code == 400 and use_json:
                use_json = False  # server may not support response_format
                continue
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:160]}",
                               transient=r.status_code in (429, 500, 502, 503, 504))
            break
        try:
            data = r.json()
            text = data["choices"][0]["message"].get("content") or ""
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"bad response body: {e}")
        u = data.get("usage") or {}
        usage = {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
                 "total": u.get("total_tokens"), "thinking": None}
        return Raw(text, data.get("model"), usage)


def make_backend(cfg: Config):
    return OpenAIBackend(cfg) if cfg.provider == "openai_compatible" else GeminiBackend(cfg)


# ---------------------------------------------------------------- chain
_PROCESS_CHAINS: dict = {}


def _cache_path() -> Path:
    return store.state_dir() / "models.json"


def build_chain(cfg: Config, backend, use_cache: bool = True) -> Chain:
    key = cfg.key()
    now = time.time()
    if use_cache:
        try:
            c = json.loads(_cache_path().read_text())
            if c.get("key") == key and now - c.get("ts", 0) < CACHE_TTL:
                ch = Chain(**c["chain"])
                ch.source = "cache"
                age = int(now - c["ts"])
                ch.log = [f"model chain loaded from cache (age {age}s): {', '.join(ch.models)}"] + _filter_log(ch)
                return ch
        except Exception:  # noqa: BLE001
            pass
    ch = Chain()
    try:
        names = backend.list_models(DISCOVERY_TIMEOUT)
        seen: list[str] = []
        for n in names:
            n = norm_name(n)
            if n not in seen:
                seen.append(n)
        ch.discovered = seen
        ch.log.append(f"discovered {len(seen)} models: {', '.join(seen) if seen else '(none)'}")
        discovered_ok = True
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", None) or f"{type(e).__name__}: {e}"
        ch.log.append(f"discovery failed: {str(reason)[:180]}, using .env names")
        ch.source = "env"
        ch.discovered = [n for n in (cfg.primary, cfg.fallback) if n]
        discovered_ok = False
    kept, dropped = [], []
    for n in ch.discovered:
        ok, rule = small_rule(n, cfg)
        (kept if ok else dropped).append((n, rule))
    ch.rules = {n: r for n, r in kept}
    ch.dropped = [[n, r] for n, r in dropped]
    order: list[str] = []
    for n in (cfg.primary, cfg.fallback):
        if n and n in ch.rules and n not in order:
            order.append(n)
    order += [n for n, _ in kept if n not in order]
    ch.models = order
    for n in (cfg.primary, cfg.fallback):
        if n and discovered_ok and n not in ch.discovered:
            ch.log.append(f"warning: configured model '{n}' is not offered by the provider; ignored")
    ch.log += _filter_log(ch)
    if not ch.models:
        raise NoSmallModel("no small model available. discovered: "
                           + (", ".join(ch.discovered) or "(none)") + "\n" + "\n".join(ch.log))
    if discovered_ok:
        try:
            store.state_dir().mkdir(parents=True, exist_ok=True)
            _cache_path().write_text(json.dumps({"key": key, "ts": now, "chain": ch.to_json()}))
        except Exception:  # noqa: BLE001
            pass
    return ch


def _filter_log(ch: Chain) -> list[str]:
    out = [f"small-model filter: kept {len(ch.models)} of {len(ch.discovered)}: "
           + (", ".join(f"{n} ({ch.rules[n]})" for n in ch.models) or "(none)")]
    if ch.dropped:
        out.append("dropped (not small): " + ", ".join(f"{n} ({r})" for n, r in ch.dropped))
    return out


# ---------------------------------------------------------------- LLM
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


@dataclass
class CallResult:
    text: str
    parsed: object
    model: str
    record: dict


class LLM:
    def __init__(self, cfg: Config | None = None, backend=None):
        self.cfg = cfg or load_config()
        self.backend = backend or make_backend(self.cfg)
        self.chain: Chain | None = None

    def ensure_chain(self, trace=None, use_cache: bool = True) -> Chain:
        if self.chain is None:
            key = (self.cfg.key(), id(self.backend))
            if use_cache and key in _PROCESS_CHAINS:
                self.chain = _PROCESS_CHAINS[key]
            else:
                self.chain = build_chain(self.cfg, self.backend, use_cache=use_cache)
                _PROCESS_CHAINS[key] = self.chain
        if trace is not None:
            for line in self.chain.log:
                trace.code(line)
            trace.code("model chain (call order): " + " -> ".join(self.chain.models))
        return self.chain

    def _assert_small(self, model: str) -> None:
        if self.chain is None or model not in self.chain.models:
            raise PermissionError(f"refusing to call '{model}': not in the small-model chain")

    def call(self, area: str, system: str, user: str, *, trace, deadline: Deadline,
             validate=None, max_tokens: int = 2048) -> CallResult:
        chain = self.ensure_chain(None)
        shown_input = f"[system]\n{system}\n[user]\n{user}"
        last = "no attempt made"
        for idx, model in enumerate(chain.models):
            nxt = chain.models[idx + 1] if idx + 1 < len(chain.models) else None
            tries = 0
            while True:
                tries += 1
                if not trace.can_call():
                    raise BudgetExceeded(f"model-call budget of {trace.max_calls} reached")
                t = min(CALL_TIMEOUT, deadline.remaining())
                if t < 1.0:
                    raise DeadlineExceeded("run deadline reached before a model call could finish")
                self._assert_small(model)
                t0 = time.monotonic()
                try:
                    raw = run_with_timeout(
                        lambda m=model, tt=t: self.backend.generate(m, system, user, max_tokens, tt), t)
                except TimeoutError:
                    err = LLMError(f"timeout after {t:.0f}s", transient=True, timeout=True)
                except LLMError as e:
                    err = e
                except Exception as e:  # noqa: BLE001
                    err = GeminiBackend._wrap(e)
                else:
                    err = None
                if err is not None:
                    dt = time.monotonic() - t0
                    retry = (err.transient and tries < 2 and deadline.remaining() > 8
                             and (not err.timeout or nxt is None))
                    trace.code(f"model {model} failed ({err.reason}, {dt:.1f}s) -> "
                               + (f"retrying {model} once" if retry else f"trying {nxt}" if nxt else "no further model"))
                    last = f"{model}: {err.reason}"
                    if retry:
                        time.sleep(min(1.0, deadline.remaining() / 10))
                        continue
                    break
                clean = strip_think(raw.text)
                parsed, perr = clean, None
                if validate is not None:
                    try:
                        parsed = validate(clean)
                    except Exception as e:  # noqa: BLE001
                        perr = e
                rec = trace.call(area, raw.model, shown_input, raw.text, raw.usage, True, requested=model)
                if perr is None:
                    return CallResult(clean, parsed, raw.model or model, rec)
                trace.code(f"model {model} returned unusable output ({str(perr)[:120]}) -> "
                           + (f"trying {nxt}" if nxt else "no further model"))
                last = f"{model}: unusable output ({str(perr)[:80]})"
                break
        raise LLMError(f"all models failed; last: {last}")


# ---------------------------------------------------------------- selftest
def selftest() -> int:
    load_env()
    cfg = load_config()
    print(f"provider: {cfg.provider}" + (f"  base_url: {cfg.base_url}" if cfg.base_url else ""))
    print(f"SMALL_MODEL_EXACT={cfg.exact} INCLUDE={cfg.include} EXCLUDE={cfg.exclude}")
    if not (cfg.exact or cfg.include or is_local(cfg)):
        print("WARNING: no small-model list configured. The organizers' list must be confirmed "
              "and put in SMALL_MODEL_* (see .env.example). Only localhost models qualify without it.")
    llm = LLM(cfg)
    try:
        ch = llm.ensure_chain(None, use_cache=False)
    except NoSmallModel as e:
        print(str(e))
        print("BONUS-SAFE: no")
        return 1
    print("\n".join(ch.log))
    print("final chain: " + " -> ".join(ch.models))
    all_small = True
    for m in ch.models:
        ok, rule = small_rule(m, cfg)
        all_small &= ok
    from .trace import Trace
    rc = 0
    for m in ch.models[:2]:
        t0 = time.monotonic()
        try:
            raw = run_with_timeout(lambda mm=m: llm.backend.generate(
                mm, "Reply with the single word: ok", "ping", 64, CALL_TIMEOUT, json_mode=False), CALL_TIMEOUT)
            dt = time.monotonic() - t0
            tk = raw.usage
            f = lambda v: store.NR if v is None else v  # noqa: E731
            print(f"call {m}: reported model={raw.model or 'not reported'} latency={dt:.2f}s "
                  f"tokens in={f(tk.get('input'))} out={f(tk.get('output'))} total={f(tk.get('total'))} "
                  f"reply={strip_think(raw.text)[:40]!r}")
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"call {m}: FAILED {getattr(e, 'reason', e)} after {time.monotonic() - t0:.1f}s")
    print(f"BONUS-SAFE: {'yes' if all_small and ch.models else 'no'}")
    return rc


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("usage: python -m engine.llm --selftest")
