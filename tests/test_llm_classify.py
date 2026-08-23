"""Offline tests for the LLM fallback classifier (no live API calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_categories  # noqa: E402
from src.llm_classify import LLMClassifier  # noqa: E402
from src.models import Article  # noqa: E402


def make(enabled=False):
    settings = {"classify": {"fallback_category": "uncategorized",
                             "llm_fallback": {"enabled": enabled}}}
    return LLMClassifier(load_categories(), settings)


def test_disabled_is_noop():
    llm = make(enabled=False)
    ok, reason = llm.available()
    assert not ok and reason == "disabled in settings"
    arts = [Article(title="x", url="u", source="s", domain="cyber",
                    categories=["uncategorized"])]
    assert llm.classify_uncategorized(arts) == 0
    assert arts[0].categories == ["uncategorized"]  # untouched


def test_catalog_contains_valid_tags():
    llm = make()
    assert "cyber.malware" in llm._allowed
    assert "ai.model_releases" in llm._allowed
    assert "cyber.malware" in llm._catalog  # rendered into the prompt catalogue


def test_parse_plain_json():
    llm = make()
    out = llm._parse('{"0": ["cyber.malware"], "1": ["ai.ethics"]}')
    assert out == {0: ["cyber.malware"], 1: ["ai.ethics"]}


def test_parse_tolerates_code_fence_and_prose():
    llm = make()
    text = 'Here you go:\n```json\n{"0": ["cyber.breaches"]}\n```\nHope that helps!'
    assert llm._parse(text) == {0: ["cyber.breaches"]}


def test_parse_garbage_returns_empty():
    llm = make()
    assert llm._parse("no json here") == {}
    assert llm._parse("") == {}
