import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
POLICY_LINKS = {
    "LICENSE": "[License](LICENSE)",
    "CONTRIBUTING.md": "[Contributing](CONTRIBUTING.md)",
    "SECURITY.md": "[Security policy](SECURITY.md)",
}
PROJECT_URLS = {
    "Repository": "https://github.com/randomparity/hmc-mcp",
    "Contributing": "https://github.com/randomparity/hmc-mcp/blob/main/CONTRIBUTING.md",
    "Security": "https://github.com/randomparity/hmc-mcp/security/policy",
}
PRIVATE_ADVISORY_URL = "https://github.com/randomparity/hmc-mcp/security/advisories/new"
VIOS_BACKUP_TOOLS = (
    "hmc_list_vios_backups",
    "hmc_backup_vios",
    "hmc_restore_vios",
)
COUNT_NUMBER = r"(?:\*{2})?\d[\d,]*(?:\*{2})?"
TOOL_COUNT_NOUN = r"(?:tools?(?:\s+names?)?|names?)"
NUMBERED_TOOL_COUNT = (
    rf"{COUNT_NUMBER}(?:\s+[\w-]+){{0,3}}\s+{TOOL_COUNT_NOUN}"
)
FIXED_TOOL_COUNT = re.compile(
    rf"(?:{NUMBERED_TOOL_COUNT}[^.\n]{{0,80}}\bevery ordinary tool\b"
    rf"|\bevery ordinary tool\b[^.\n]{{0,80}}(?:{COUNT_NUMBER}\s+total\b"
    rf"|{NUMBERED_TOOL_COUNT}))",
    re.IGNORECASE,
)


def _project_metadata() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        document = tomllib.load(file)
    return document["project"]


def test_project_declares_mit_license_and_policy_urls() -> None:
    project = _project_metadata()

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    classifiers = project.get("classifiers", [])
    assert "License :: OSI Approved :: MIT License" not in classifiers
    assert project["urls"] == PROJECT_URLS


def test_readme_links_canonical_governance_files() -> None:
    readme = (ROOT / "README.md").read_text()

    for path, link in POLICY_LINKS.items():
        assert (ROOT / path).is_file(), f"missing canonical policy file: {path}"
        assert link in readme, f"README must link {path}"


def test_readme_documents_the_typed_facade_and_its_covered_surface() -> None:
    readme = (ROOT / "README.md").read_text()
    library = readme.split("## Reusable Python API", 1)[1].split("## Configure", 1)[0]

    assert "PEP 561" in library
    assert "py.typed" in library
    for covered in ("dataclass", "literal alias", "signature"):
        assert covered in library


def test_vios_backup_hmc_floor_is_published_without_narrowing_general_support() -> None:
    readme = (ROOT / "README.md").read_text()
    cheatsheet = (ROOT / "docs" / "hmc-cli-cheatsheet.md").read_text()
    compatibility = readme.split("## HMC version compatibility", 1)[1].split(
        "### Firmware write-path compatibility", 1
    )[0]

    assert "HMC V8 through V11" in compatibility
    assert "HMC V10 or newer" in compatibility
    for tool in VIOS_BACKUP_TOOLS:
        assert tool in compatibility
        assert tool in cheatsheet
    assert "require HMC V10 or newer" in cheatsheet


def test_access_policy_guidance_matches_connectionless_dispatch_semantics() -> None:
    readme = (ROOT / "README.md").read_text()
    connectionless = " ".join(
        readme.split("### What the policy does not bound", 1)[1]
        .split("### Migrating to a required access policy", 1)[0]
        .split()
    )

    assert "Every MCP tool is dispatch-wrapped" in connectionless
    assert "connection dimension is vacuous" in connectionless
    assert "by tool or effect class" in connectionless
    assert 'targets = "all-targets"' in connectionless
    assert "withhold" in connectionless.lower()


def test_generated_policy_guidance_does_not_pin_a_stale_tool_count() -> None:
    readme = (ROOT / "README.md").read_text()
    adr = (
        ROOT
        / "docs"
        / "adr"
        / "0041-fail-closed-startup-and-legacy-policy-generation.md"
    ).read_text()
    migration = readme.split("### Migrating to a required access policy", 1)[1].split(
        "### Narrowing `targets`", 1
    )[0]
    current_adr_guidance = adr.split(
        "- **The generator is the onboarding path for fresh installs too", 1
    )[1].split(
        "- **The audit record tells the caller what the denial deliberately does not", 1
    )[0]

    assert "every ordinary tool" in migration
    assert "every ordinary tool" in current_adr_guidance
    for guidance in (migration, current_adr_guidance):
        assert not FIXED_TOOL_COUNT.search(guidance)

        for fixed_count in (
            "136 tool names, every ordinary tool",
            "129 names, every ordinary tool",
            "**136** tools, every ordinary tool",
            "every ordinary tool is named explicitly (136 total)",
        ):
            stale_guidance = guidance.replace("every ordinary tool", fixed_count, 1)
            assert FIXED_TOOL_COUNT.search(stale_guidance)

    assert not FIXED_TOOL_COUNT.search(
        "Issue #289 requires every ordinary tool to be named explicitly"
    )
    assert not FIXED_TOOL_COUNT.search("31 total non-exhaustive tools")


def test_contribution_guide_defines_the_complete_local_path() -> None:
    guide = (ROOT / "CONTRIBUTING.md").read_text()

    for command in (
        "just setup",
        "just verify",
        "UV_NO_SYNC=1 uv run prek run --all-files",
    ):
        assert f"`{command}`" in guide
    assert "focused" in guide.lower()
    assert "test" in guide.lower()
    assert "pull request" in guide.lower()
    assert "[security policy](security.md)" in guide.lower()


def test_security_policy_uses_only_private_vulnerability_reporting() -> None:
    policy = (ROOT / "SECURITY.md").read_text()

    assert PRIVATE_ADVISORY_URL in policy
    assert "do not open a public issue" in policy.lower()
    assert (
        "do not include passwords, access tokens, production data, or other secrets"
        in policy.lower()
    )
