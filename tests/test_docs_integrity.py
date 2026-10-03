"""Pure offline guard against truncated project documentation and stale SDK keys."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "docs" / "GROWW_API_MEMO.md",
    ROOT / "docs" / "GROWW_SDK_GUARD_OPERATIONS.md",
)
GUARD_KEYS = (
    "GROWW_SDK_MIN_START_GAP_SECONDS",
    "GROWW_SDK_MAX_CALLS_PER_CHILD",
    "GROWW_SDK_CALL_TELEMETRY",
)


def test_project_markdown_has_balanced_fences_and_one_readme_title():
    for path in DOCUMENTS:
        body = path.read_text(encoding="utf-8")
        fences = [line for line in body.splitlines() if re.match(r"^\s*```", line)]
        assert len(fences) % 2 == 0, f"Unclosed code fence in {path.relative_to(ROOT)}"
        assert body.endswith("\n"), f"Missing terminal newline in {path.relative_to(ROOT)}"
    readme = DOCUMENTS[0].read_text(encoding="utf-8")
    assert readme.count("# Trading_App\n") == 1


def test_documented_guard_keys_match_checked_in_example():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    guide = DOCUMENTS[2].read_text(encoding="utf-8")
    for key in GUARD_KEYS:
        assert re.search(rf"^{key}=", example, flags=re.MULTILINE)
        assert key in guide
    assert "not a shared account-wide rate limiter" in guide.lower()
