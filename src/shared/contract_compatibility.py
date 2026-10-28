"""Conservative structural compatibility checks against a released contract tag or directory."""

import argparse
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

import yaml

PATCH, MINOR, MAJOR = 1, 2, 3
ANNOTATIONS = {"title", "description", "$comment", "examples", "example", "summary"}


def version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value.strip()):
        raise ValueError(f"Invalid contract version: {value!r}")
    return tuple(map(int, value.strip().split(".")))


def classify(before, after, path: str = "") -> tuple[int, list[str]]:
    if before == after:
        return 0, []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        named_entries = path.endswith(("/properties", "/$defs", "/paths"))
        for key in before.keys() | after.keys():
            location = f"{path}/{key}"
            if key in ANNOTATIONS and not named_entries:
                if before.get(key) != after.get(key):
                    changes.append((PATCH, [location]))
            elif key not in after:
                changes.append((MAJOR, [f"{location}: removed"]))
            elif key not in before:
                level = MINOR if named_entries else MAJOR
                changes.append((level, [f"{location}: added"]))
            elif key == "enum" and not named_entries:
                old, new = set(before[key]), set(after[key])
                level = MAJOR if old - new else MINOR if new - old else PATCH
                changes.append((level, [f"{location}: code set changed"]))
            elif key == "required" and not named_entries and set(before[key]) == set(after[key]):
                changes.append((PATCH, [f"{location}: reordered"]))
            elif path == "/info" and key == "version":
                changes.append((PATCH, [location]))
            else:
                changes.append(classify(before[key], after[key], location))
        return (
            max((level for level, _ in changes), default=0),
            [reason for _, reasons in changes for reason in reasons],
        )
    return MAJOR, [f"{path}: constraint or semantic structure changed"]


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }


def compare(before: dict[str, str], after: dict[str, str]) -> tuple[int, list[str]]:
    old, new = version(before["VERSION"]), version(after["VERSION"])
    if new < old:
        raise ValueError("Contract VERSION cannot decrease from the released baseline")
    changes = []
    for name in sorted(before.keys() | after.keys()):
        if name in {"VERSION", "CHANGELOG.md"}:
            continue
        if before.get(name) == after.get(name):
            continue
        if name.startswith("examples/") or name.endswith(".md"):
            changes.append((PATCH, [name]))
        elif name not in after:
            changes.append((MAJOR, [f"{name}: removed"]))
        elif name not in before:
            changes.append((MINOR, [f"{name}: added"]))
        elif name.endswith(".schema.json"):
            level, reasons = classify(json.loads(before[name]), json.loads(after[name]))
            changes.append((level, [name + reason for reason in reasons]))
        elif name.endswith((".yaml", ".yml")):
            level, reasons = classify(yaml.safe_load(before[name]), yaml.safe_load(after[name]))
            changes.append((level, [name + reason for reason in reasons]))
        else:
            changes.append((MAJOR, [f"{name}: changed"]))
    level = max((level for level, _ in changes), default=0)
    if not level:
        return 0, []
    sufficient = (
        new > old and (level < MAJOR or new[0] > old[0]) and (level < MINOR or new[:2] > old[:2])
    )
    if not sufficient:
        raise ValueError(
            f"Contract changes require a {['', 'patch', 'minor', 'major'][level]} bump"
        )
    if before.get("CHANGELOG.md") == after.get("CHANGELOG.md"):
        raise ValueError("Contract changes require a CHANGELOG.md update")
    return level, [reason for _, reasons in changes for reason in reasons]


def released_snapshot(repository: Path) -> tuple[str | None, dict[str, str] | None]:
    result = subprocess.run(
        ["git", "tag", "--list", "contracts-v*", "--sort=-version:refname"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    tags = [
        tag for tag in result.stdout.splitlines() if re.fullmatch(r"contracts-v\d+\.\d+\.\d+", tag)
    ]
    if not tags:
        return None, None
    tag = tags[0]
    archive = subprocess.run(
        ["git", "archive", "--format=tar", tag, "contracts"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        for member in tar:
            if member.isfile() and member.name.startswith("contracts/"):
                stream = tar.extractfile(member)
                if stream is not None:
                    files[member.name.removeprefix("contracts/")] = stream.read().decode("utf-8")
    if version(files["VERSION"]) != version(tag.removeprefix("contracts-v")):
        raise ValueError("Released contract tag and VERSION disagree")
    return tag, files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, help="Local baseline contracts directory")
    args = parser.parse_args()
    repository = Path.cwd()
    if args.baseline:
        name, before = str(args.baseline), snapshot(args.baseline)
    else:
        name, before = released_snapshot(repository)
    if before is None:
        print(
            "No released contracts-vX.Y.Z tag: checking draft only, not claiming release approval"
        )
        return
    level, reasons = compare(before, snapshot(repository / "contracts"))
    print(f"Compatibility against {name}: {['unchanged', 'patch', 'minor', 'major'][level]}")
    for reason in reasons:
        print(f"  {reason}")


if __name__ == "__main__":
    main()
