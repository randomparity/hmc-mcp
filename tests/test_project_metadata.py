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
PRIVATE_ADVISORY_URL = (
    "https://github.com/randomparity/hmc-mcp/security/advisories/new"
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
