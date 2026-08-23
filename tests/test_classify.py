"""Tests for the rule-based classifier."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classify import Classifier  # noqa: E402
from src.config import load_categories, load_settings  # noqa: E402
from src.models import Article  # noqa: E402


def make_classifier() -> Classifier:
    return Classifier(load_categories(), load_settings())


def art(title: str, summary: str = "", domain: str = "cyber") -> Article:
    return Article(title=title, url=f"https://x/{hash(title)}", source="t",
                   domain=domain, summary=summary)


def test_cve_tagged_vulnerability():
    c = make_classifier()
    a = c.classify(art("New CVE-2026-1234 RCE in Acme VPN", "critical zero-day"))
    assert any("vulnerabilities" in t for t in a.categories)
    assert a.severity == "critical"


def test_ransomware_tagged_malware():
    c = make_classifier()
    a = c.classify(art("LockBit ransomware hits hospital"))
    assert any("malware" in t for t in a.categories)


def test_ai_model_release():
    c = make_classifier()
    a = c.classify(art("Anthropic launches new Claude model", domain="ai"))
    assert any("model_releases" in t for t in a.categories)


def test_prompt_injection_intersection():
    c = make_classifier()
    a = c.classify(art("Prompt injection attack bypasses LLM security", domain="both"))
    assert any("ai_security" in t for t in a.categories)


def test_word_boundary_no_false_positive():
    c = make_classifier()
    # "cover" must not match "cve"
    a = c.classify(art("Company announces new insurance cover for staff", domain="ai"))
    assert not any("vulnerabilities" in t for t in a.categories)


def test_fallback_when_no_match():
    c = make_classifier()
    a = c.classify(art("A pleasant walk in the park", domain="ai"))
    assert a.categories == ["uncategorized"]


def test_region_detection():
    c = make_classifier()
    a = c.classify(art("NCSC UK issues guidance", "united kingdom advisory"))
    assert "uk" in a.regions


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["python", "-m", "pytest", __file__, "-v"]))
