import copy
from pathlib import Path

import pytest

from shared.seed import load_seed

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def seed_base():
    return load_seed(ROOT / "seed" / "seed.json", ROOT / "contracts")


@pytest.fixture
def snapshot(seed_base):
    return seed_base.clone()


class FakeDataverse:
    """Unit-test-only optimistic concurrency fake; deliberately not an installed runtime adapter."""

    def __init__(self, rows):
        self.rows = {row["caseId"]: (copy.deepcopy(row), 1) for row in rows}

    def read(self, case_id):
        row, version = self.rows[case_id]
        return copy.deepcopy(row), f'W/"{version}"'

    def update(self, case_id, changes, *, if_match):
        row, etag = self.read(case_id)
        if if_match != etag:
            raise ValueError("ETag conflict: re-read and reconsider")
        if "caseId" in changes and changes["caseId"] != case_id:
            raise ValueError("Cannot change a case identity")
        _, version = self.rows[case_id]
        self.rows[case_id] = ({**row, **copy.deepcopy(changes)}, version + 1)
        return self.read(case_id)


@pytest.fixture
def dataverse_fake(snapshot):
    return FakeDataverse(snapshot.rows("CloseOutCase"))
