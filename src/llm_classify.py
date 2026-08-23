"""Optional LLM fallback classification.

Runs ONLY on articles the rule-based classifier left uncategorized, so it costs
nothing on the common path. Uses google-generativeai (Gemini) with GOOGLE_API_KEY,
matching the project's existing gcp-llm-mcp setup.

Fully optional and graceful:
  - disabled by default (classify.llm_fallback.enabled)
  - no-op if the library isn't installed or no API key is found
  - invalid / hallucinated tags are dropped (validated against the taxonomy)

Config (settings.yaml):
  classify:
    llm_fallback:
      enabled: false
      model: gemini-3.6-flash
      batch_size: 10
      max_items: 40        # cap per refresh to bound cost
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .models import Article

ROOT = Path(__file__).resolve().parent.parent


class LLMClassifier:
    def __init__(self, categories_cfg: dict, settings: dict):
        cfg = settings.get("classify", {}).get("llm_fallback", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.model_name = cfg.get("model", "gemini-3.6-flash")
        self.batch_size = int(cfg.get("batch_size", 10))
        self.max_items = int(cfg.get("max_items", 40))
        self.fallback_tag = settings.get("classify", {}).get(
            "fallback_category", "uncategorized"
        )
        self._allowed, self._catalog = self._build_catalog(categories_cfg)
        self._model = None  # lazily constructed

    # ---- public ----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """(usable, reason). Cheap checks only; does not import the SDK."""
        if not self.enabled:
            return False, "disabled in settings"
        if not self._api_key():
            return False, "no GOOGLE_API_KEY"
        return True, "ok"

    def classify_uncategorized(self, articles: list[Article], verbose: bool = False) -> int:
        ok, reason = self.available()
        if not ok:
            if verbose and self.enabled:
                print(f"  LLM fallback skipped: {reason}")
            return 0

        targets = [a for a in articles
                   if a.categories == [self.fallback_tag]][: self.max_items]
        if not targets:
            return 0
        if not self._ensure_model():
            if verbose:
                print("  LLM fallback skipped: google-generativeai not installed")
            return 0

        rescued = 0
        for i in range(0, len(targets), self.batch_size):
            batch = targets[i:i + self.batch_size]
            try:
                tags_by_idx = self._classify_batch(batch)
            except Exception as exc:  # noqa: BLE001 - never let the LLM break refresh
                if verbose:
                    print(f"  LLM batch failed: {exc}")
                continue
            for idx, tags in tags_by_idx.items():
                valid = [t for t in tags if t in self._allowed and t != self.fallback_tag]
                if 0 <= idx < len(batch) and valid:
                    batch[idx].categories = valid
                    batch[idx].classified_by = "llm"
                    rescued += 1
        return rescued

    # ---- internals -------------------------------------------------------
    @staticmethod
    def _build_catalog(categories_cfg: dict) -> tuple[set[str], str]:
        allowed: set[str] = set()
        lines: list[str] = []
        for domain, cats in categories_cfg.get("categories", {}).items():
            for key, spec in cats.items():
                tag = f"{domain}.{key}"
                allowed.add(tag)
                lines.append(f"- {tag}: {spec.get('label', key)}")
        return allowed, "\n".join(lines)

    def _api_key(self) -> str | None:
        key = os.environ.get("GOOGLE_API_KEY")
        if key:
            return key
        # Fall back to a .env in the project or the claudecore root.
        for env_path in (ROOT / ".env", ROOT.parent.parent / ".env"):
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("GOOGLE_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        return None

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        try:
            import google.generativeai as genai  # noqa: PLC0415
        except ImportError:
            return False
        genai.configure(api_key=self._api_key())
        self._model = genai.GenerativeModel(self.model_name)
        return True

    def _classify_batch(self, batch: list[Article]) -> dict[int, list[str]]:
        items = "\n".join(
            f"[{i}] {a.title} — {(a.summary or '')[:200]}"
            for i, a in enumerate(batch)
        )
        prompt = (
            "You tag cybersecurity and AI news into a fixed taxonomy.\n"
            "Choose 1-3 best-matching category tags for each item from THIS list "
            "only (use the exact tag text):\n"
            f"{self._catalog}\n\n"
            "If an item genuinely fits none, use [\"uncategorized\"].\n"
            "Return ONLY a JSON object mapping each item index (as a string) to an "
            'array of tags, e.g. {"0": ["cyber.malware"], "1": ["ai.ethics"]}.\n\n'
            f"Items:\n{items}"
        )
        resp = self._model.generate_content(prompt)
        return self._parse(resp.text)

    @staticmethod
    def _parse(text: str) -> dict[int, list[str]]:
        if not text:
            return {}
        m = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate code fences / prose
        if not m:
            return {}
        raw = json.loads(m.group(0))
        out: dict[int, list[str]] = {}
        for k, v in raw.items():
            try:
                idx = int(k)
            except (ValueError, TypeError):
                continue
            if isinstance(v, list):
                out[idx] = [str(t).strip() for t in v]
        return out
