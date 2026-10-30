"""Local, nonsecret installer selections and restart-safe progress."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]


class Blocked(RuntimeError):
    def __init__(self, owner: str, action: str, resource: str = ""):
        self.owner, self.action, self.resource = owner, action, resource
        super().__init__(f"{owner}: {action}" + (f"\nTarget: {resource}" if resource else ""))


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: str = Field(pattern=r"^[a-z][a-z0-9-]{1,23}$")
    subscriptionId: str
    tenantId: str
    operatorId: str
    resourceGroup: str
    foundryName: str
    deploymentName: str
    fabric: dict = Field(default_factory=dict)
    items: dict = Field(default_factory=dict)
    apiClientId: str | None = None
    apiObjectId: str | None = None
    dispatchPrincipalId: str | None = None
    dispatchAppId: str | None = None
    callbackConnectionId: str | None = None
    projectorOwnerId: str | None = None
    approvals: dict = Field(default_factory=dict)

    @field_validator(
        "subscriptionId",
        "tenantId",
        "operatorId",
        "apiClientId",
        "apiObjectId",
        "dispatchPrincipalId",
        "dispatchAppId",
        "callbackConnectionId",
        "projectorOwnerId",
    )
    @classmethod
    def identifier(cls, value):
        return str(UUID(value)) if value is not None else value

    @field_validator("resourceGroup", "deploymentName")
    @classmethod
    def resource_name(cls, value):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
            raise ValueError("An exact resource group or deployment name is required")
        return value

    @field_validator("foundryName")
    @classmethod
    def foundry_name(cls, value):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", value):
            raise ValueError("An exact Foundry resource name is required")
        return value


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_hash(*patterns: str) -> str:
    files = sorted({path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()})
    return digest(
        {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        }
    )


def confirm(message: str) -> bool:
    return input(f"\n{message}\nContinue? [y/N]: ").strip().lower() in {"y", "yes"}


def select(message: str, rows: list[dict], label="displayName") -> dict:
    if not rows:
        raise Blocked("Target owner", f"Make an existing {message.lower()} available to this user.")
    print(f"\n{message}")
    for index, row in enumerate(rows, 1):
        print(f"  {index}. {row.get(label, row['id'])} ({row['id']})")
    while True:
        answer = input("Select a number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1]
        print("Enter one of the numbers above.")


def text(message: str, default: str = "", pattern: str | None = None) -> str:
    while True:
        value = input(message + (f" [{default}]" if default else "") + ": ").strip() or default
        if value and (pattern is None or re.fullmatch(pattern, value)):
            return value
        print("Enter a value in the requested format. Do not enter a password or access key.")


def az(*args: str):
    executable = shutil.which("az")
    if not executable:
        raise Blocked("Operator", "Install Azure CLI, reopen PowerShell and run the same command.")
    result = subprocess.run(
        [executable, *args, "--only-show-errors", "--output", "json"],
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # CLI/service errors can echo credentials or application settings.
        resource = next(
            (
                args[args.index(flag) + 1]
                for flag in ("--url", "--scope", "--resource-group", "--name", "--id", "--location")
                if flag in args
            ),
            "",
        )
        directory = args[0] == "ad" or resource.startswith("https://graph.microsoft.com/")
        raise Blocked(
            "Entra application administrator" if directory else "Azure administrator",
            f"Resolve the failed Azure operation ({' '.join(args[:3])}); "
            + (
                "grant the signed-in user the required directory/application permission, "
                "or perform this application setup for them, then rerun."
                if directory
                else "check resource/role-assignment access, quota and target policy, then rerun."
            ),
            resource,
        )
    return json.loads(result.stdout) if result.stdout.strip() else None


class Progress:
    def __init__(self, environment: str):
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,23}", environment):
            raise ValueError("Environment must be 2-24 lowercase letters, digits or hyphens.")
        self.directory = ROOT / ".local" / "installer" / environment
        self.directory.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.directory / "profile.json"
        self.path = self.directory / "state.json"
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        self.profile = (
            Profile.model_validate_json(self.profile_path.read_text("utf-8"))
            if self.profile_path.exists()
            else None
        )
        if self.profile and self.profile.environment != environment:
            raise Blocked("Operator", "Use the environment name belonging to this saved profile.")
        self.environment = environment

    @contextmanager
    def lock(self):
        with (self.directory / "installer.lock").open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Blocked(
                    "Operator", "Wait for the other installer for this environment to exit."
                ) from error
            try:
                yield self
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def save_profile(self):
        atomic_json(self.profile_path, self.profile.model_dump())

    def save(self):
        self.data["updatedAt"] = datetime.now(UTC).isoformat()
        atomic_json(self.path, self.data)

    def done(self, phase: str, desired: str):
        self.data.setdefault("phases", {})[phase] = desired
        self.save()

    def matches(self, phase: str, desired: str) -> bool:
        return self.data.get("phases", {}).get(phase) == desired

    def approve(self, operation: str, desired, message: str):
        fingerprint = digest(desired)
        if self.profile.approvals.get(operation) != fingerprint:
            if not confirm(message):
                raise Blocked(
                    "Operator / target owner", message + "\nComplete or approve this, then rerun."
                )
            self.profile.approvals[operation] = fingerprint
            self.save_profile()

    def phase(self, name):
        self.data["phase"] = name
        self.save()
        print(f"\n{name}...")

    def bind(self, key, target):
        fingerprint = digest(target)
        if self.data.get(key) and self.data[key] != fingerprint:
            raise Blocked(
                "Operator",
                "Saved target selections changed outside guided setup. Restore the original "
                "profile or use a new environment name; nothing will be redirected silently.",
            )
        self.data[key] = fingerprint
        self.save()

    def handoff(self, error: Blocked):
        message = (
            f"Setup paused. {error}\n\n"
            f"After that, rerun: .\\scripts\\deploy-demo.ps1 -Environment {self.environment}\n"
            "Completed setup is retained. No end-to-end verification has been performed.\n"
        )
        (self.directory / "handoff.txt").write_text(message, encoding="utf-8")
        print("\n" + message)
