import base64
import configparser
import csv
import hashlib
import re
import stat
import sys
import tarfile
import tomllib
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import IO, Never

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version


PROJECT_NAME = "hmc-mcp"
PACKAGE_NAME = "hmc_mcp"
CORE_METADATA_VERSION = "2.5"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
SINGLETON_METADATA = (
    "Metadata-Version",
    "Name",
    "Version",
    "Requires-Python",
    "License-Expression",
)
SDIST_INPUTS = (
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "scripts/versioning.py",
)


class ValidationError(ValueError):
    pass


class _CaseConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


@dataclass(frozen=True)
class Distribution:
    name: str
    version: str
    metadata: Message
    members: dict[str, bytes]


@dataclass(frozen=True)
class ProjectConfiguration:
    name: str
    requires_python: str
    license_expression: str
    dependencies: set[str]
    scripts: dict[str, str]


def _fail(artifact: str, invariant: str) -> Never:
    raise ValidationError(f"{artifact}: {invariant}")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _valid_version(value: str, artifact: str) -> str:
    try:
        return str(Version(value))
    except InvalidVersion:
        _fail(artifact, "version must be valid PEP 440")


def _validate_member_name(name: str, artifact: str) -> str:
    if unicodedata.normalize("NFC", name) != name:
        _fail(artifact, f"archive member path is not NFC: {name!r}")
    if "\\" in name or name.startswith("/"):
        _fail(artifact, f"archive member path is not portable: {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(artifact, f"archive member path is not canonical: {name!r}")
    if ":" in parts[0]:
        _fail(artifact, f"archive member path is drive-like: {name!r}")
    return str(PurePosixPath(*parts))


def _check_archive_input(path: Path) -> None:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        _fail(path.name, "archive input exceeds 256 MiB")


def _read_bounded(
    stream: IO[bytes],
    artifact: str,
    member: str,
    *,
    declared_size: int,
    total: int,
) -> bytes:
    if declared_size > MAX_MEMBER_BYTES:
        _fail(artifact, f"archive member exceeds 64 MiB uncompressed: {member}")
    if total + declared_size > MAX_TOTAL_BYTES:
        _fail(artifact, "archive exceeds 512 MiB total uncompressed")
    data = bytearray()
    while chunk := stream.read(READ_CHUNK_BYTES):
        data.extend(chunk)
        if len(data) > MAX_MEMBER_BYTES:
            _fail(artifact, f"archive member exceeds 64 MiB uncompressed: {member}")
        if total + len(data) > MAX_TOTAL_BYTES:
            _fail(artifact, "archive exceeds 512 MiB total uncompressed")
    return bytes(data)


def _read_wheel(path: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    declared_total = 0
    total = 0
    try:
        _check_archive_input(path)
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            if len(items) > MAX_ARCHIVE_MEMBERS:
                _fail(path.name, "archive contains more than 4096 members")
            for item in items:
                name = _validate_member_name(item.filename, path.name)
                unix_type = (
                    stat.S_IFMT(item.external_attr >> 16)
                    if item.create_system == 3
                    else 0
                )
                if item.is_dir() or unix_type not in {0, stat.S_IFREG}:
                    _fail(path.name, f"wheel member must be a regular file: {name}")
                if name in members:
                    _fail(path.name, f"duplicate archive member: {name}")
                declared_total += item.file_size
                if declared_total > MAX_TOTAL_BYTES:
                    _fail(path.name, "archive exceeds 512 MiB total uncompressed")
                with archive.open(item) as stream:
                    members[name] = _read_bounded(
                        stream,
                        path.name,
                        name,
                        declared_size=item.file_size,
                        total=total,
                    )
                total += len(members[name])
    except (
        OSError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
    ) as error:
        _fail(path.name, f"wheel archive is malformed: {type(error).__name__}")
    return members


def _read_sdist(path: Path) -> tuple[str, dict[str, bytes]]:
    members: dict[str, bytes] = {}
    root: str | None = None
    declared_total = 0
    total = 0
    try:
        _check_archive_input(path)
        with tarfile.open(path, mode="r:gz") as archive:
            for count, item in enumerate(archive, start=1):
                if count > MAX_ARCHIVE_MEMBERS:
                    _fail(path.name, "archive contains more than 4096 members")
                name = _validate_member_name(item.name, path.name)
                if item.issym() or item.islnk():
                    _validate_member_name(item.linkname, path.name)
                    _fail(path.name, f"sdist links are forbidden: {name}")
                if not item.isfile():
                    _fail(path.name, f"sdist member must be a regular file: {name}")
                parts = name.split("/", 1)
                if len(parts) != 2:
                    _fail(
                        path.name, f"sdist member lacks one top-level directory: {name}"
                    )
                root = root or parts[0]
                if parts[0] != root or parts[1] in members:
                    _fail(path.name, f"sdist root or member is not unique: {name}")
                declared_total += item.size
                if declared_total > MAX_TOTAL_BYTES:
                    _fail(path.name, "archive exceeds 512 MiB total uncompressed")
                extracted = archive.extractfile(item)
                if extracted is None:
                    _fail(path.name, f"sdist member is unreadable: {name}")
                members[parts[1]] = _read_bounded(
                    extracted,
                    path.name,
                    name,
                    declared_size=item.size,
                    total=total,
                )
                total += len(members[parts[1]])
    except (OSError, tarfile.TarError) as error:
        _fail(path.name, f"sdist archive is malformed: {type(error).__name__}")
    if root is None:
        _fail(path.name, "sdist archive is empty")
    return root, members


def _parse_message(data: bytes, artifact: str, member: str) -> Message:
    try:
        message = BytesParser(policy=default).parsebytes(data)
    except Exception as error:
        _fail(artifact, f"{member} is malformed: {type(error).__name__}")
    if message.defects:
        _fail(artifact, f"{member} has parser defects")
    return message


def _metadata(data: bytes, artifact: str, member: str) -> Message:
    message = _parse_message(data, artifact, member)
    for field in SINGLETON_METADATA:
        if len(message.get_all(field, [])) != 1:
            _fail(artifact, f"{member} must contain exactly one {field}")
    if message["Metadata-Version"] != CORE_METADATA_VERSION:
        _fail(artifact, f"{member} Metadata-Version must be {CORE_METADATA_VERSION}")
    return message


def _requirements(message: Message, artifact: str) -> set[str]:
    try:
        return {
            str(Requirement(value)) for value in message.get_all("Requires-Dist", [])
        }
    except Exception as error:
        _fail(artifact, f"Requires-Dist is malformed: {type(error).__name__}")


def _project_configuration(root: Path) -> ProjectConfiguration:
    try:
        with (root / "pyproject.toml").open("rb") as file:
            document = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        _fail(
            "pyproject.toml", f"project metadata is unreadable: {type(error).__name__}"
        )
    project = document.get("project")
    if not isinstance(project, dict):
        _fail("pyproject.toml", "project must be a table")

    def required_string(field: str) -> str:
        value = project.get(field)
        if not isinstance(value, str) or not value:
            _fail("pyproject.toml", f"project.{field} must be a non-empty string")
        return value

    name = required_string("name")
    requires_python = required_string("requires-python")
    license_expression = required_string("license")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(value, str) for value in dependencies
    ):
        _fail("pyproject.toml", "project.dependencies must be a list of strings")
    scripts = project.get("scripts")
    if (
        not isinstance(scripts, dict)
        or not scripts
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in scripts.items()
        )
    ):
        _fail("pyproject.toml", "project.scripts must be a non-empty string mapping")
    try:
        normalized = {str(Requirement(value)) for value in dependencies}
    except InvalidRequirement:
        _fail("pyproject.toml", "project.dependencies contains an invalid requirement")
    return ProjectConfiguration(
        name, requires_python, license_expression, normalized, scripts
    )


def _validate_metadata(
    distribution: Distribution,
    project: ProjectConfiguration,
    artifact: str,
) -> None:
    message = distribution.metadata
    if _canonical_name(message["Name"]) != PROJECT_NAME:
        _fail(artifact, "project name is inconsistent")
    if _valid_version(message["Version"], artifact) != distribution.version:
        _fail(artifact, "metadata version is inconsistent")
    expected_name = _canonical_name(project.name)
    if (
        expected_name != PROJECT_NAME
        or message["Requires-Python"] != project.requires_python
    ):
        _fail(artifact, "project name or Requires-Python differs from pyproject.toml")
    if message["License-Expression"] != project.license_expression:
        _fail(artifact, "license expression differs from pyproject.toml")
    if _requirements(message, artifact) != project.dependencies:
        _fail(artifact, "runtime dependencies differ from pyproject.toml")


def _parse_wheel_filename(path: Path) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"(?P<name>[^-]+)-(?P<version>[^-]+)-(?P<tag>[^-]+-[^-]+-[^-]+)\.whl",
        path.name,
    )
    if match is None:
        _fail(path.name, "wheel filename is malformed")
    return (
        _canonical_name(match["name"]),
        _valid_version(match["version"], path.name),
        match["tag"],
    )


def _parse_sdist_filename(path: Path) -> tuple[str, str]:
    match = re.fullmatch(r"(?P<name>.+)-(?P<version>[^-]+)\.tar\.gz", path.name)
    if match is None:
        _fail(path.name, "sdist filename is malformed")
    return _canonical_name(match["name"]), _valid_version(match["version"], path.name)


def _dist_info(members: dict[str, bytes], artifact: str) -> str:
    roots = {name.split("/", 1)[0] for name in members if ".dist-info/" in name}
    if len(roots) != 1:
        _fail(artifact, "wheel must contain exactly one .dist-info directory")
    root = roots.pop()
    if not root.endswith(".dist-info"):
        _fail(artifact, "wheel metadata directory is malformed")
    return root


def _wheel_record(members: dict[str, bytes], root: str, artifact: str) -> None:
    record_name = f"{root}/RECORD"
    try:
        rows = list(csv.reader(members[record_name].decode().splitlines()))
    except (KeyError, UnicodeDecodeError, csv.Error) as error:
        _fail(artifact, f"RECORD is missing or malformed: {type(error).__name__}")
    if any(len(row) != 3 for row in rows) or len({row[0] for row in rows}) != len(rows):
        _fail(artifact, "RECORD rows must be unique triples")
    records = {row[0]: (row[1], row[2]) for row in rows}
    if set(records) != set(members):
        _fail(artifact, "RECORD member set is inconsistent")
    for name, data in members.items():
        digest, size = records[name]
        if name == record_name:
            if digest or size:
                _fail(artifact, "RECORD self-row must omit digest and size")
            continue
        encoded = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode()
        )
        if digest != f"sha256={encoded}" or size != str(len(data)):
            _fail(artifact, f"RECORD digest or size differs for {name}")


def _wheel_file(members: dict[str, bytes], root: str, tag: str, artifact: str) -> None:
    message = _parse_message(members.get(f"{root}/WHEEL", b""), artifact, "WHEEL")
    for field in ("Wheel-Version", "Root-Is-Purelib"):
        if len(message.get_all(field, [])) != 1:
            _fail(artifact, f"WHEEL must contain exactly one {field}")
    if message["Wheel-Version"] != "1.0" or message["Root-Is-Purelib"] != "true":
        _fail(artifact, "WHEEL version or purelib flag is unsupported")
    tags = message.get_all("Tag", [])
    if (
        not tags
        or len(tags) != len(set(tags))
        or set(tags) != {tag}
        or tag != "py3-none-any"
    ):
        _fail(artifact, "WHEEL tags differ from the pure-Python filename tag")


def _scripts_from_wheel(data: bytes, artifact: str) -> dict[str, str]:
    parser = _CaseConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode())
        if parser.sections() != ["console_scripts"]:
            _fail(artifact, "entry_points.txt contains undeclared groups")
        return dict(parser["console_scripts"])
    except (UnicodeDecodeError, configparser.Error, KeyError) as error:
        _fail(artifact, f"entry_points.txt is malformed: {type(error).__name__}")


def _source_members(root: Path) -> dict[str, bytes]:
    package = root / "src" / PACKAGE_NAME
    members = {
        path.relative_to(root / "src").as_posix(): path.read_bytes()
        for path in package.rglob("*.py")
        if path.is_file()
    }
    for sentinel in (f"{PACKAGE_NAME}/__init__.py", f"{PACKAGE_NAME}/server.py"):
        if sentinel not in members:
            _fail("source checkout", f"missing package sentinel: {sentinel}")
    return members


def _validate_wheel(
    path: Path,
    project_root: Path,
    project: ProjectConfiguration,
) -> Distribution:
    name, version, tag = _parse_wheel_filename(path)
    members = _read_wheel(path)
    root = _dist_info(members, path.name)
    expected_root = f"{PACKAGE_NAME}-{version}.dist-info"
    if name != PROJECT_NAME or root != expected_root:
        _fail(path.name, "wheel filename and .dist-info identity differ")
    metadata = _metadata(members.get(f"{root}/METADATA", b""), path.name, "METADATA")
    distribution = Distribution(name, version, metadata, members)
    _validate_metadata(distribution, project, path.name)
    _wheel_file(members, root, tag, path.name)
    _wheel_record(members, root, path.name)
    source = _source_members(project_root)
    expected = set(source) | {
        f"{root}/METADATA",
        f"{root}/WHEEL",
        f"{root}/entry_points.txt",
        f"{root}/licenses/LICENSE",
        f"{root}/RECORD",
    }
    if set(members) != expected:
        _fail(path.name, "wheel member set is not closed")
    for member, data in source.items():
        if members[member] != data:
            _fail(path.name, f"package bytes differ for {member}")
    if members[f"{root}/licenses/LICENSE"] != (project_root / "LICENSE").read_bytes():
        _fail(path.name, "wheel license bytes differ from checkout")
    if (
        _scripts_from_wheel(members[f"{root}/entry_points.txt"], path.name)
        != project.scripts
    ):
        _fail(path.name, "wheel console scripts differ from pyproject.toml")
    return distribution


def _validate_sdist(
    path: Path,
    project_root: Path,
    project: ProjectConfiguration,
) -> Distribution:
    name, version = _parse_sdist_filename(path)
    root, members = _read_sdist(path)
    expected_root = f"{PACKAGE_NAME}-{version}"
    if name != PROJECT_NAME or root != expected_root:
        _fail(path.name, "sdist filename and root identity differ")
    metadata = _metadata(members.get("PKG-INFO", b""), path.name, "PKG-INFO")
    distribution = Distribution(name, version, metadata, members)
    _validate_metadata(distribution, project, path.name)
    source = _source_members(project_root)
    sdist_source = {f"src/{name}": data for name, data in source.items()}
    expected = set(sdist_source) | set(SDIST_INPUTS) | {"PKG-INFO"}
    if set(members) != expected:
        _fail(path.name, "sdist member set is not closed")
    for member, data in sdist_source.items():
        if members[member] != data:
            _fail(path.name, f"package bytes differ for {member}")
    for member in SDIST_INPUTS:
        if members[member] != (project_root / member).read_bytes():
            _fail(path.name, f"sdist input bytes differ for {member}")
    try:
        embedded = tomllib.loads(members["pyproject.toml"].decode())
        scripts = embedded["project"]["scripts"]
    except (UnicodeDecodeError, KeyError, tomllib.TOMLDecodeError) as error:
        _fail(
            path.name, f"embedded pyproject.toml is malformed: {type(error).__name__}"
        )
    if scripts != project.scripts:
        _fail(path.name, "sdist console scripts differ from checkout")
    return distribution


def validate(artifact_dir: Path, project_root: Path) -> None:
    if not artifact_dir.is_dir():
        _fail(str(artifact_dir), "artifact directory does not exist")
    if not project_root.is_dir():
        _fail(str(project_root), "project root does not exist")
    artifacts = [path for path in artifact_dir.iterdir() if path.name != ".gitignore"]
    wheels = [path for path in artifacts if path.name.endswith(".whl")]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(artifacts) != 2 or len(wheels) != 1 or len(sdists) != 1:
        _fail(str(artifact_dir), "expected exactly one wheel and one .tar.gz sdist")
    project = _project_configuration(project_root)
    wheel = _validate_wheel(wheels[0], project_root, project)
    sdist = _validate_sdist(sdists[0], project_root, project)
    if wheel.name != sdist.name or wheel.version != sdist.version:
        _fail(str(artifact_dir), "wheel and sdist identity or version differs")


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 2:
        print(
            "usage: validate_release_artifacts.py ARTIFACT_DIR PROJECT_ROOT",
            file=sys.stderr,
        )
        return 2
    try:
        validate(Path(args[0]), Path(args[1]))
    except (OSError, ValidationError) as error:
        print(f"artifact validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
