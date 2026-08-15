import os
import base64
import csv
import hashlib
import io
import shutil
import stat
import subprocess
import tarfile
import re
import zipfile
from pathlib import Path
from typing import Callable

import pytest

from validate_release_artifacts import main


ROOT = Path(__file__).parents[1]


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        args,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _clean_project(project: Path) -> Path:
    project.mkdir(exist_ok=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    for relative in filter(None, tracked):
        source = ROOT / relative
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for command in (
        ("git", "init", "--quiet"),
        ("git", "config", "user.email", "tests@example.invalid"),
        ("git", "config", "user.name", "Artifact Tests"),
        ("git", "add", "."),
        ("git", "commit", "--quiet", "-m", "fixture"),
    ):
        result = _run(*command, cwd=project)
        assert result.returncode == 0, result.stderr
    return project


@pytest.fixture(scope="module")
def built_project(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    project = _clean_project(tmp_path_factory.mktemp("release-project"))

    artifacts = project / "dist"
    result = _run(
        "uv",
        "build",
        "--wheel",
        "--sdist",
        "--out-dir",
        str(artifacts),
        ".",
        cwd=project,
    )
    assert result.returncode == 0, result.stderr
    return artifacts, project


def test_validates_clean_wheel_and_sdist(built_project: tuple[Path, Path]) -> None:
    artifacts, project = built_project

    assert main([str(artifacts), str(project)]) == 0


def _artifact_copy(
    tmp_path: Path,
    built_project: tuple[Path, Path],
) -> tuple[Path, Path]:
    artifacts, project = built_project
    copied = tmp_path / "dist"
    shutil.copytree(artifacts, copied)
    return copied, project


def _record_bytes(members: dict[str, bytes]) -> bytes:
    record = next(name for name in members if name.endswith(".dist-info/RECORD"))
    rows: list[list[str]] = []
    for name, data in members.items():
        if name == record:
            rows.append([name, "", ""])
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode()}", str(len(data))])
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    return stream.getvalue().encode()


def _rewrite_wheel(
    path: Path,
    mutate: Callable[[dict[str, bytes]], None],
    *,
    repair_record: bool = True,
) -> None:
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    mutate(members)
    if repair_record:
        record = next(name for name in members if name.endswith(".dist-info/RECORD"))
        members[record] = _record_bytes(members)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _rewrite_wheel_mode(path: Path, suffix: str, mode: int) -> None:
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    record = next(name for name in members if name.endswith(".dist-info/RECORD"))
    members[record] = _record_bytes(members)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            if name.endswith(suffix):
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = mode << 16
                archive.writestr(item, data)
            else:
                archive.writestr(name, data)


def _tar_data(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if not member.isfile():
        return b""
    extracted = archive.extractfile(member)
    assert extracted is not None
    return extracted.read()


def _rewrite_sdist(
    path: Path,
    mutate: Callable[[list[tuple[tarfile.TarInfo, bytes]]], None],
) -> None:
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, _tar_data(archive, member)) for member in archive.getmembers()]
    mutate(entries)
    with tarfile.open(path, "w:gz") as archive:
        for member, data in entries:
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)


def _assert_invalid(
    artifacts: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    invariant: str,
) -> None:
    assert main([str(artifacts), str(project)]) == 1
    assert invariant in capsys.readouterr().err


def test_rejects_wrong_cli_arity(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "ARTIFACT_DIR PROJECT_ROOT" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["artifacts", "project"])
def test_rejects_missing_input_directory(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    artifacts, project = built_project
    absent = tmp_path / "absent"
    arguments = [str(absent), str(project)] if missing == "artifacts" else [str(artifacts), str(absent)]

    assert main(arguments) == 1
    assert "does not exist" in capsys.readouterr().err


def test_rejects_unexpected_artifact(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    (artifacts / "unexpected.zip").write_bytes(b"not an artifact")

    _assert_invalid(artifacts, project, capsys, "exactly one wheel")


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
def test_rejects_missing_artifact(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    artifact: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    pattern = "*.whl" if artifact == "wheel" else "*.tar.gz"
    next(artifacts.glob(pattern)).rename(tmp_path / f"removed-{artifact}")

    _assert_invalid(artifacts, project, capsys, "exactly one wheel")


def test_rejects_duplicate_wheel(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))
    shutil.copy2(wheel, artifacts / f"duplicate-{wheel.name}")

    _assert_invalid(artifacts, project, capsys, "exactly one wheel")


@pytest.mark.parametrize(
    ("identity", "invariant"),
    [
        ("other", "wheel filename and .dist-info identity differ"),
        ("version", "wheel filename and .dist-info identity differ"),
    ],
)
def test_rejects_wheel_filename_identity_mismatch(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    identity: str,
    invariant: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))
    parts = wheel.name.split("-")
    parts[0 if identity == "other" else 1] = "other" if identity == "other" else "9.9.9"
    wheel.rename(artifacts / "-".join(parts))

    _assert_invalid(artifacts, project, capsys, invariant)


def test_rejects_record_digest_mismatch(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def corrupt_record(members: dict[str, bytes]) -> None:
        record = next(name for name in members if name.endswith(".dist-info/RECORD"))
        members[record] = members[record].replace(b"sha256=", b"sha256=broken", 1)

    _rewrite_wheel(wheel, corrupt_record, repair_record=False)
    _assert_invalid(artifacts, project, capsys, "RECORD digest or size differs")


@pytest.mark.parametrize(
    ("mutation", "invariant"),
    [
        ("missing", "RECORD is missing or malformed"),
        ("extra", "RECORD member set is inconsistent"),
        ("duplicate", "RECORD rows must be unique triples"),
        ("size", "RECORD digest or size differs"),
    ],
)
def test_rejects_invalid_record_structure(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    invariant: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_record(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/RECORD"))
        if mutation == "missing":
            del members[name]
            return
        rows = list(csv.reader(members[name].decode().splitlines()))
        if mutation == "extra":
            rows.append(["absent.py", "", ""])
        elif mutation == "duplicate":
            rows.append(rows[0])
        else:
            row = next(row for row in rows if row[0] != name)
            row[2] = str(int(row[2]) + 1)
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(rows)
        members[name] = stream.getvalue().encode()

    _rewrite_wheel(wheel, change_record, repair_record=False)
    _assert_invalid(artifacts, project, capsys, invariant)


def test_rejects_byte_divergent_wheel_package_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_package(members: dict[str, bytes]) -> None:
        members["hmc_mcp/__init__.py"] += b"\n# changed\n"

    _rewrite_wheel(wheel, change_package)
    _assert_invalid(artifacts, project, capsys, "package bytes differ")


def test_rejects_unexpected_wheel_payload_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))
    _rewrite_wheel(wheel, lambda members: members.__setitem__("other/payload", b"payload"))

    _assert_invalid(artifacts, project, capsys, "wheel member set is not closed")


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.py",
        "C:/drive.py",
        "back\\slash.py",
        "empty//component.py",
        "dot/./component.py",
        "unicode/cafe\u0301.py",
    ],
)
def test_rejects_noncanonical_wheel_path(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    member_name: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))
    _rewrite_wheel(wheel, lambda members: members.__setitem__(member_name, b"payload"))

    _assert_invalid(artifacts, project, capsys, "archive member path")


@pytest.mark.parametrize(
    ("suffix", "mode"),
    [
        ("hmc_mcp/__init__.py", stat.S_IFLNK | 0o777),
        (".dist-info/WHEEL", stat.S_IFIFO | 0o644),
    ],
)
def test_rejects_non_regular_wheel_member(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    mode: int,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    _rewrite_wheel_mode(wheel, suffix, mode)

    _assert_invalid(artifacts, project, capsys, "wheel member must be a regular file")


@pytest.mark.parametrize(
    ("mutation", "invariant"),
    [
        ("missing", "WHEEL must contain exactly one Wheel-Version"),
        ("missing_tag", "WHEEL tags differ"),
        ("duplicate_version", "WHEEL must contain exactly one Wheel-Version"),
        ("version", "WHEEL version or purelib flag is unsupported"),
        ("purelib", "WHEEL version or purelib flag is unsupported"),
        ("tag", "WHEEL tags differ"),
        ("duplicate_tag", "WHEEL tags differ"),
    ],
)
def test_rejects_invalid_wheel_contract_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    invariant: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_wheel(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/WHEEL"))
        if mutation == "missing":
            del members[name]
        elif mutation == "missing_tag":
            members[name] = members[name].replace(b"Tag: py3-none-any\n", b"")
        elif mutation == "duplicate_version":
            members[name] += b"Wheel-Version: 1.0\n"
        elif mutation == "version":
            members[name] = members[name].replace(b"Wheel-Version: 1.0", b"Wheel-Version: 2.0")
        elif mutation == "purelib":
            members[name] = members[name].replace(b"Root-Is-Purelib: true", b"Root-Is-Purelib: false")
        elif mutation == "duplicate_tag":
            members[name] += b"Tag: py3-none-any\n"
        else:
            members[name] = members[name].replace(b"Tag: py3-none-any", b"Tag: cp311-none-any")

    _rewrite_wheel(wheel, change_wheel)
    _assert_invalid(artifacts, project, capsys, invariant)


@pytest.mark.parametrize("member_suffix", ["METADATA", "PKG-INFO"])
def test_rejects_duplicate_core_metadata_singleton(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    member_suffix: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    if member_suffix == "METADATA":
        wheel = next(artifacts.glob("*.whl"))

        def duplicate_name(members: dict[str, bytes]) -> None:
            name = next(item for item in members if item.endswith(".dist-info/METADATA"))
            members[name] = b"Name: conflicting\n" + members[name]

        _rewrite_wheel(wheel, duplicate_name)
    else:
        sdist = next(artifacts.glob("*.tar.gz"))

        def duplicate_name(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
            for index, (member, data) in enumerate(entries):
                if member.name.endswith("/PKG-INFO"):
                    member.size += len(b"Name: conflicting\n")
                    entries[index] = (member, b"Name: conflicting\n" + data)

        _rewrite_sdist(sdist, duplicate_name)
    _assert_invalid(artifacts, project, capsys, "exactly one Name")


@pytest.mark.parametrize(
    "field",
    ["Name", "Version", "Requires-Python", "License-Expression"],
)
def test_rejects_missing_core_metadata_singleton(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    field: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def remove_field(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/METADATA"))
        lines = members[name].splitlines(keepends=True)
        members[name] = b"".join(line for line in lines if not line.startswith(f"{field}:".encode()))

    _rewrite_wheel(wheel, remove_field)
    _assert_invalid(artifacts, project, capsys, f"exactly one {field}")


@pytest.mark.parametrize("member_suffix", ["METADATA", "PKG-INFO"])
def test_rejects_unsupported_core_metadata_version(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    member_suffix: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    if member_suffix == "METADATA":
        wheel = next(artifacts.glob("*.whl"))

        def change_version(members: dict[str, bytes]) -> None:
            name = next(item for item in members if item.endswith(".dist-info/METADATA"))
            members[name] = members[name].replace(b"Metadata-Version: 2.5", b"Metadata-Version: 1.0")

        _rewrite_wheel(wheel, change_version)
    else:
        sdist = next(artifacts.glob("*.tar.gz"))

        def change_version(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
            for index, (member, data) in enumerate(entries):
                if member.name.endswith("/PKG-INFO"):
                    changed = data.replace(b"Metadata-Version: 2.5", b"Metadata-Version: 1.0")
                    member.size = len(changed)
                    entries[index] = (member, changed)

        _rewrite_sdist(sdist, change_version)
    _assert_invalid(artifacts, project, capsys, "Metadata-Version must be 2.5")


@pytest.mark.parametrize(
    ("field", "old", "new", "invariant"),
    [
        ("Name", b"Name: hmc-mcp", b"Name: other", "project name is inconsistent"),
        (
            "Requires-Python",
            b"Requires-Python: >=3.11",
            b"Requires-Python: >=3.12",
            "Requires-Python differs",
        ),
        (
            "License-Expression",
            b"License-Expression: MIT",
            b"License-Expression: Apache-2.0",
            "license expression differs",
        ),
        (
            "Requires-Dist",
            b"Requires-Dist: asyncssh==2.24.0",
            b"Requires-Dist: asyncssh==2.23.0",
            "runtime dependencies differ",
        ),
    ],
)
def test_rejects_wheel_metadata_mismatch_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    field: str,
    old: bytes,
    new: bytes,
    invariant: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_metadata(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/METADATA"))
        assert old in members[name], field
        members[name] = members[name].replace(old, new, 1)

    _rewrite_wheel(wheel, change_metadata)
    _assert_invalid(artifacts, project, capsys, invariant)


def test_rejects_wheel_entry_point_mismatch_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_entry_point(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/entry_points.txt"))
        members[name] = members[name].replace(b"hmc_mcp:main", b"hmc_mcp:missing")

    _rewrite_wheel(wheel, change_entry_point)
    _assert_invalid(artifacts, project, capsys, "console scripts differ")


@pytest.mark.parametrize("mutation", ["case", "extra-group"])
def test_rejects_nonexact_wheel_entry_points_with_valid_record(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    wheel = next(artifacts.glob("*.whl"))

    def change_entry_points(members: dict[str, bytes]) -> None:
        name = next(item for item in members if item.endswith(".dist-info/entry_points.txt"))
        if mutation == "case":
            members[name] = members[name].replace(b"hmc-mcp =", b"HMC-MCP =")
        else:
            members[name] += b"\n[other]\ncommand = hmc_mcp:main\n"

    _rewrite_wheel(wheel, change_entry_points)
    expected = "console scripts differ" if mutation == "case" else "undeclared groups"
    _assert_invalid(artifacts, project, capsys, expected)


@pytest.mark.parametrize(
    "member_type",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.FIFOTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.DIRTYPE,
    ],
)
def test_rejects_non_regular_sdist_member(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    member_type: bytes,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def add_link(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        root = entries[0][0].name.split("/", 1)[0]
        member = tarfile.TarInfo(f"{root}/unsafe-member")
        member.type = member_type
        if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
            member.linkname = "../../outside"
        entries.append((member, b""))

    _rewrite_sdist(sdist, add_link)
    expected = "archive member path is not canonical" if member_type in {
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
    } else "sdist member must be a regular file"
    _assert_invalid(artifacts, project, capsys, expected)


def test_rejects_sdist_link_with_safe_target(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def add_link(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        root = entries[0][0].name.split("/", 1)[0]
        member = tarfile.TarInfo(f"{root}/safe-looking-link")
        member.type = tarfile.SYMTYPE
        member.linkname = "README.md"
        entries.append((member, b""))

    _rewrite_sdist(sdist, add_link)
    _assert_invalid(artifacts, project, capsys, "sdist links are forbidden")


def test_rejects_byte_divergent_sdist_package(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def change_package(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        for index, (member, data) in enumerate(entries):
            if member.name.endswith("/src/hmc_mcp/__init__.py"):
                changed = data + b"\n# changed\n"
                member.size = len(changed)
                entries[index] = (member, changed)

    _rewrite_sdist(sdist, change_package)
    _assert_invalid(artifacts, project, capsys, "package bytes differ")


@pytest.mark.parametrize("mutation", ["missing", "bytes", "nonregular"])
def test_rejects_invalid_required_sdist_input(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def change_input(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        index = next(
            index
            for index, (member, _) in enumerate(entries)
            if member.name.endswith("/README.md")
        )
        member, data = entries[index]
        if mutation == "missing":
            entries.pop(index)
        elif mutation == "bytes":
            changed = data + b"\nchanged\n"
            member.size = len(changed)
            entries[index] = (member, changed)
        else:
            member.type = tarfile.FIFOTYPE
            member.size = 0
            entries[index] = (member, b"")

    _rewrite_sdist(sdist, change_input)
    expected = {
        "missing": "sdist member set is not closed",
        "bytes": "sdist input bytes differ",
        "nonregular": "sdist member must be a regular file",
    }[mutation]
    _assert_invalid(artifacts, project, capsys, expected)


def test_rejects_sdist_entry_point_mismatch(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def change_pyproject(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        for index, (member, data) in enumerate(entries):
            if member.name.endswith("/pyproject.toml"):
                changed = data.replace(b'hmc-mcp = "hmc_mcp:main"', b'hmc-mcp = "hmc_mcp:missing"')
                member.size = len(changed)
                entries[index] = (member, changed)

    _rewrite_sdist(sdist, change_pyproject)
    _assert_invalid(artifacts, project, capsys, "sdist input bytes differ")


@pytest.mark.parametrize("mutation", ["missing-name", "missing-scripts", "dependencies-type", "bad-requirement"])
def test_rejects_malformed_project_configuration_actionably(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    artifacts, project = built_project
    malformed = tmp_path / "project"
    shutil.copytree(project, malformed, ignore=shutil.ignore_patterns(".git", ".venv", "dist"))
    pyproject = malformed / "pyproject.toml"
    content = pyproject.read_text()
    if mutation == "missing-name":
        content = content.replace('name = "hmc-mcp"', 'renamed = "hmc-mcp"')
    elif mutation == "missing-scripts":
        content = content.replace('[project.scripts]\nhmc-mcp = "hmc_mcp:main"\n', "")
    elif mutation == "dependencies-type":
        content = re.sub(r"dependencies = \[\n.*?\n\]", 'dependencies = "wrong"', content, count=1, flags=re.DOTALL)
    else:
        content = content.replace('"asyncssh==2.24.0"', '"not a valid requirement !!!"')
    pyproject.write_text(content)

    assert main([str(artifacts), str(malformed)]) == 1
    error = capsys.readouterr().err
    assert "project metadata is invalid" in error
    assert "Traceback" not in error


def test_rejects_unexpected_regular_sdist_member(
    tmp_path: Path,
    built_project: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts, project = _artifact_copy(tmp_path, built_project)
    sdist = next(artifacts.glob("*.tar.gz"))

    def add_file(entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        root = entries[0][0].name.split("/", 1)[0]
        member = tarfile.TarInfo(f"{root}/unexpected.txt")
        member.size = 7
        entries.append((member, b"payload"))

    _rewrite_sdist(sdist, add_file)
    _assert_invalid(artifacts, project, capsys, "sdist member set is not closed")


def test_clean_checkout_runs_canonical_artifact_commands(tmp_path: Path) -> None:
    project = _clean_project(tmp_path / "project")

    for command in (
        ("just", "setup"),
        ("just", "build"),
        ("just", "verify-artifacts"),
    ):
        result = _run(*command, cwd=project)
        assert result.returncode == 0, result.stderr
    assert len(list((project / "dist").glob("*.whl"))) == 1
    assert len(list((project / "dist").glob("*.tar.gz"))) == 1


def test_dirty_checkout_build_fails_with_actionable_provenance(tmp_path: Path) -> None:
    project = _clean_project(tmp_path / "project")
    (project / "dirty.txt").write_text("dirty")

    result = _run("just", "build", cwd=project)

    assert result.returncode != 0
    assert "Git repository is dirty" in result.stderr
    assert "commit or clean staged, unstaged, and untracked files" in result.stderr
