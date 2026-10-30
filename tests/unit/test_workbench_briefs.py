import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from agents.workbench import fixture_cases, handler, site_scope
from agents.workbench_briefs import MAX_BRIEF_BYTES, BriefReader

RUN = "00000000-0000-4000-8000-000000000001"
TRANSITION = "00000000-0000-4000-8000-000000000002"
SCOPE = site_scope("SE-STO-0042", fixture_cases())
NAME = f"coordinator/{RUN}/{SCOPE['caseId']}/Blocked/{TRANSITION}/workflow123.md"
CONTENT = b"# Close-out review - SE-STO-0042\n\nA recommendation, not an approval."


def saved_blob(name=NAME):
    return SimpleNamespace(
        name=name,
        metadata={
            "siteid": SCOPE["siteId"],
            "caseid": SCOPE["caseId"],
            "program": SCOPE["program"],
            "readiness": "Blocked",
            "runid": RUN,
            "workflowrunid": "workflow123",
            "factsrevision": "a" * 64,
            "sourceref": f"fabric:headless:{RUN}:{SCOPE['siteId']}:{'a' * 64}",
            "responseid": "resp_test",
            "agentversion": "18",
            "briefversion": "1",
        },
        last_modified=datetime(2026, 10, 30, 16, tzinfo=UTC),
        size=len(CONTENT),
    )


class Container:
    def __init__(self):
        self.blobs = [saved_blob(), saved_blob(NAME.replace(".md", ".json"))]
        self.reads = []
        self.content = CONTENT

    def list_blobs(self, **kwargs):
        assert kwargs == {"name_starts_with": "coordinator/", "include": ["metadata"]}
        return self.blobs

    def get_blob_client(self, name):
        self.reads.append(name)
        return self

    def download_blob(self, **kwargs):
        assert kwargs == {"offset": 0, "length": MAX_BRIEF_BYTES + 1}
        return SimpleNamespace(readall=lambda: self.content)


def test_list_only_scoped_markdown_without_reading_raw_or_other_site_responses():
    container = Container()
    other = copy.deepcopy(saved_blob())
    other.name = NAME.replace(SCOPE["caseId"], "00000000-0000-4000-8000-000000999999")
    container.blobs.append(other)
    reader = BriefReader(container)
    rows = reader.list(SCOPE)
    assert len(rows) == 1
    assert rows[0] == {
        "id": NAME,
        "siteId": SCOPE["siteId"],
        "readiness": "Blocked",
        "savedAt": "2026-10-30T16:00:00+00:00",
    }
    assert container.reads == []
    assert reader.read(NAME, SCOPE)["markdown"] == CONTENT.decode()
    assert container.reads == [NAME]
    assert reader.list(site_scope("SE-GOT-0017", fixture_cases())) == []


@pytest.mark.parametrize(
    "name",
    [
        "../other.md",
        NAME.replace(".md", ".json"),
        "https://example.test/brief.md",
        NAME.replace(SCOPE["caseId"], "00000000-0000-4000-8000-000000000017"),
        NAME + "?token=private",
        NAME.replace("coordinator/", "coordinator-oauth-probe/"),
    ],
)
def test_read_rejects_unscoped_paths_urls_raw_json_and_probes_before_blob_access(name):
    container = Container()
    with pytest.raises(ValueError):
        BriefReader(container).read(name, SCOPE)
    assert container.reads == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("siteid", "SE-GOT-0017"),
        ("caseid", "another-case"),
        ("program", "another-program"),
        ("briefversion", "2"),
        ("readiness", "Ready"),
        ("responseid", "not-a-response"),
        ("agentversion", "latest"),
        ("runid", TRANSITION),
        ("workflowrunid", "other-run"),
        ("sourceref", "fabric:wrong-revision"),
        ("factsrevision", "unhashed"),
    ],
)
def test_inconsistent_publication_metadata_is_an_error_not_an_empty_success(field, value):
    container = Container()
    container.blobs[0].metadata[field] = value
    with pytest.raises(RuntimeError, match="metadata"):
        BriefReader(container).list(SCOPE)
    assert container.reads == []


@pytest.mark.parametrize(
    "content",
    [
        b"{}",
        b"# Close-out review - SE-GOT-0017",
        b"\xff",
        b"# Close-out review - SE-STO-0042\n" + b"x" * MAX_BRIEF_BYTES,
    ],
    ids=["json", "wrong-site", "invalid-utf8", "too-large"],
)
def test_invalid_or_oversized_saved_text_is_not_presented_as_a_brief(content):
    container = Container()
    container.content = content
    with pytest.raises(RuntimeError):
        BriefReader(container).read(NAME, SCOPE)


def test_missing_brief_is_not_fabricated():
    container = Container()
    container.blobs = []
    with pytest.raises(FileNotFoundError):
        BriefReader(container).read(NAME, SCOPE)


@contextmanager
def api(briefs):
    with ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(None, fixture_cases(), briefs=briefs)
    ) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/api/presenter"
        finally:
            server.shutdown()
            thread.join()


def test_brief_http_endpoints_scope_the_list_and_reader():
    with api(BriefReader(Container())) as base:
        with urlopen(f"{base}/briefs?siteId=SE-STO-0042") as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert len(json.load(response)) == 1
        query = urlencode({"siteId": SCOPE["siteId"], "briefId": NAME})
        with urlopen(f"{base}/brief?{query}") as response:
            assert json.load(response)["markdown"] == CONTENT.decode()
        with urlopen(f"{base}/briefs?siteId=SE-GOT-0017") as response:
            assert json.load(response) == []
        for path in (
            "/briefs",
            "/briefs?siteId=unsupported",
            "/briefs?siteId=SE-STO-0042&siteId=SE-GOT-0017",
            "/briefs?siteId=SE-STO-0042&url=https://example.test",
            f"/brief?{urlencode({'siteId': 'SE-GOT-0017', 'briefId': NAME})}",
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 400


def test_unconfigured_storage_is_unavailable_not_an_empty_brief_list():
    with api(None) as base, pytest.raises(HTTPError) as error:
        urlopen(f"{base}/briefs?siteId=SE-STO-0042")
    assert error.value.code == 503


def test_corrupt_storage_is_reported_as_a_server_failure():
    container = Container()
    container.blobs[0].metadata["siteid"] = "SE-GOT-0017"
    with api(BriefReader(container)) as base, pytest.raises(HTTPError) as error:
        urlopen(f"{base}/briefs?siteId=SE-STO-0042")
    assert error.value.code == 502
