import copy
import json
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import httpx
import pytest

from agents.assistant import (
    DELEGATED_AUDIENCE,
    assistant_definition,
    reconcile_delegated_connection,
)
from agents.coordinator_brief import brief_definition
from agents.workbench import (
    AssistantFailure,
    ChatRequest,
    allowed_request,
    chat_scope,
    completed_answer,
    fixture_cases,
    handler,
)


def source_definition():
    return {
        "kind": "prompt",
        "model": "existing-deployment",
        "tools": [
            {
                "type": "mcp",
                "server_label": "documents",
                "server_url": "https://example.search.windows.net/knowledgebases/shared/mcp",
                "project_connection_id": "existing-documents",
                "allowed_tools": {"tool_names": ["knowledge_base_retrieve"]},
                "require_approval": "never",
            },
            {
                "type": "fabric_iq_preview",
                "server_label": "fabric-facts",
                "server_url": "https://api.fabric.microsoft.com/v1/mcp/workspaces/demo",
                "project_connection_id": "existing-fabric",
                "require_approval": "never",
            },
            {"type": "function", "name": "unrelated_write"},
        ],
    }


def test_assistant_reuses_only_native_read_tools_without_mutating_source():
    source = source_definition()
    before = copy.deepcopy(source)
    body = assistant_definition(source)
    assert source == before
    assert body["kind"] == "prompt"
    assert body["model"] == source["model"]
    assert len(body["tools"]) == 2
    assert body["tools"][0]["allowed_tools"] == ["knowledge_base_retrieve"]
    assert body["tools"][1] == source["tools"][1]
    assert "text" not in body
    assert "never independently decide or recompute" in body["instructions"]
    assert "No approvals" in body["instructions"]
    assert "not a source of case facts" in body["instructions"]


def test_readable_coordinator_keeps_native_tools_without_changing_structured_consumers():
    source = source_definition()
    source["text"] = {"format": {"type": "json_schema", "schema": {"old": "schema"}}}
    before = copy.deepcopy(source)
    body = brief_definition(source)
    assert source == before
    assert body["model"] == source["model"]
    assert body["text"] == {"format": {"type": "text"}}
    assert len(body["tools"]) == 2
    assert body["tools"][1] == source["tools"][1]
    prompt = body["instructions"]
    for requirement in (
        "BOTH",
        "factsRevision",
        "greatest validatedAt",
        "including its hash",
        "Never infer an allowable tolerance",
        "Construction Manager approves rework",
        "not platform citation markers",
        "# Brief unavailable",
        "ONE complete Markdown",
        "Missing tolerance is NOT proof of non-compliance",
        "Never recommend\nphysical antenna movement",
        "Do not assign ticket-closure authority to the Coordinator",
        "visible label MUST include the retrieved artifact ID",
    ):
        assert requirement in prompt
    for heading in (
        "What needs attention",
        "Recommended next steps",
        "Evidence and open questions",
        "Approval boundary",
    ):
        assert f"## {heading}" in prompt
    with pytest.raises(ValueError, match="prompt-agent"):
        brief_definition({**source, "kind": "hosted"})


@pytest.mark.parametrize("change", ["missing", "extra-doc-tool", "duplicate", "not-native"])
def test_assistant_rejects_ambiguous_or_non_native_bindings(change):
    body = source_definition()
    if change == "missing":
        body["tools"].pop(0)
    elif change == "extra-doc-tool":
        body["tools"][0]["allowed_tools"]["tool_names"].append("write")
    elif change == "duplicate":
        body["tools"].append(copy.deepcopy(body["tools"][0]))
    else:
        body["tools"][1]["type"] = "mcp"
    with pytest.raises(ValueError):
        assistant_definition(body)


def test_case_views_are_labelled_seed_fixtures_and_chat_passes_only_scope():
    cases = fixture_cases()
    assert [(row["site"]["siteId"], row["readiness"]) for row in cases] == [
        ("SE-STO-0042", "Blocked"),
        ("SE-GOT-0017", "Ready"),
    ]
    assert all(row["mode"] == "stub" and row["validationSource"] == "seed" for row in cases)
    request = ChatRequest(siteId="SE-STO-0042", messages=[{"role": "user", "content": "Why?"}])
    scope = chat_scope(request, cases)
    assert set(scope) == {"siteId", "caseId", "program", "scenario", "sourceMode"}
    assert scope["scenario"] == "initial-blocked"
    assert "photos" not in scope and "readiness" not in scope
    request.siteId = "another-site"
    with pytest.raises(ValueError, match="configured"):
        chat_scope(request, cases)


def response():
    return {
        "id": "response-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Evidence-backed answer."}],
            },
            *[
                {
                    "type": "mcp_call",
                    "server_label": label,
                    "id": label,
                    "name": "retrieve",
                    "output": "source records",
                    "error": None,
                }
                for label in ("documents", "fabric-facts")
            ],
        ],
    }


def test_answer_requires_completed_text_and_actual_both_source_receipts():
    raw = response()
    answer = completed_answer(raw)
    assert answer["responseId"] == raw["id"]
    assert {row["source"] for row in answer["retrieval"]} == {"documents", "fabric-facts"}
    for status in ("failed", "incomplete", "cancelled", "in_progress"):
        with pytest.raises(AssistantFailure, match="did not complete"):
            completed_answer({**raw, "status": status})
    raw["output"].pop()
    with pytest.raises(AssistantFailure, match="both IQ"):
        completed_answer(raw)
    raw = response()
    raw["output"][1]["error"] = {"message": "denied"}
    with pytest.raises(AssistantFailure, match="Native retrieval failed"):
        completed_answer(raw)


def test_service_failure_retains_the_exact_boundary_and_response_id():
    raw = {
        "id": "response-fabric-failed",
        "status": "failed",
        "error": {"code": "tool_user_error", "message": "Fabric MCP enumeration failed"},
    }
    with pytest.raises(AssistantFailure, match="response-fabric-failed.*Fabric MCP"):
        completed_answer(raw)


def scoped_response(site="SE-STO-0042"):
    scope = chat_scope(
        ChatRequest(siteId=site, messages=[{"role": "user", "content": "Why?"}]),
        fixture_cases(),
    )
    raw = response()
    call = raw["output"][-1]
    call["arguments"] = json.dumps({"userQuestion": " ".join(scope.values())})
    call["output"] = (
        f"fabric:contract-v01:{scope['scenario']}:{scope['siteId']}:{'a' * 64} "
        f"caseId {scope['caseId']} program {scope['program']} readiness Blocked"
    )
    return raw, scope


def citation_response():
    raw, scope = scoped_response()
    raw["id"] = "resp_citations"
    url = "https://documents.example.test/iq/SE-STO-0042/rfds.txt"
    marker = "\u30106:1\u2020source\u3011"
    parts = []
    for text in (f"\U0001f4ce **RFDS** requires 120 degrees {marker}.", f"Again {marker}."):
        start = text.index(marker)
        parts.append(
            {
                "type": "output_text",
                "text": text,
                "annotations": [
                    {
                        "type": "url_citation",
                        "start_index": start,
                        "end_index": start + len(marker),
                        "url": url,
                    }
                ],
            }
        )
    raw["output"][0]["content"] = parts
    raw["output"][1]["output"] = json.dumps(
        {
            "documents": [
                {
                    "url": url,
                    "content": json.dumps({"blob_url": url, "snippet": snippet}),
                }
                for snippet in ("SYNTHETIC INPUT\nSector B azimuth 120 degrees", "Version: 1")
            ]
        }
    )
    return raw, scope, marker


def test_citations_preserve_offsets_across_text_parts_and_only_use_actual_tool_excerpts():
    raw, scope, marker = citation_response()
    answer = completed_answer(raw, scope)
    assert len(answer["citations"]) == 2
    for citation in answer["citations"]:
        assert answer["text"][citation["start"] : citation["end"]] == marker
        assert citation["title"] == "rfds.txt"
        assert citation["excerpt"] == "SYNTHETIC INPUT\nSector B azimuth 120 degrees\n\nVersion: 1"
    assert answer["citations"][0]["url"] == answer["citations"][1]["url"]


@pytest.mark.parametrize(
    "change",
    ["unretrieved", "javascript", "credentials", "wrong-blob", "missing-snippet", "bad-json"],
)
def test_citation_preview_never_invents_or_fetches_unretrieved_content(change, caplog):
    raw, scope, _ = citation_response()
    parts = raw["output"][0]["content"]
    documents = json.loads(raw["output"][1]["output"])["documents"]
    if change == "unretrieved":
        for part in parts:
            part["annotations"][0]["url"] = "https://other.example.test/secret.txt"
    elif change in {"javascript", "credentials"}:
        url = (
            "javascript:alert(1)"
            if change == "javascript"
            else "https://user:password@documents.example.test/rfds.txt"
        )
        for part in parts:
            part["annotations"][0]["url"] = url
        for document in documents:
            document["url"] = url
            document["content"] = json.dumps({"blob_url": url, "snippet": "Untrusted"})
    elif change in {"wrong-blob", "missing-snippet"}:
        for document in documents:
            content = json.loads(document["content"])
            if change == "wrong-blob":
                content["blob_url"] = "https://other.example.test/unrelated.txt"
            else:
                content.pop("snippet")
            document["content"] = json.dumps(content)
    else:
        for document in documents:
            document["content"] = "not JSON"
    raw["output"][1]["output"] = json.dumps({"documents": documents})
    assert completed_answer(raw, scope)["citations"] == []
    assert "citation" in caplog.text


@pytest.mark.parametrize("start,end", [(-1, 2), (True, 2), (1, 1000), (3, 2), ("1", 2)])
def test_invalid_citation_offsets_are_logged_without_losing_the_answer(start, end, caplog):
    raw, scope, _ = citation_response()
    raw["output"][0]["content"] = raw["output"][0]["content"][:1]
    raw["output"][0]["content"][0]["annotations"][0].update(start_index=start, end_index=end)
    answer = completed_answer(raw, scope)
    assert answer["text"]
    assert answer["citations"] == []
    assert "valid span" in caplog.text


def test_missing_malformed_and_overlapping_annotations_are_not_silently_trusted(caplog):
    raw, scope, _ = citation_response()
    parts = raw["output"][0]["content"]
    parts[0]["annotations"].append(copy.deepcopy(parts[0]["annotations"][0]))
    parts[0]["annotations"].append(None)
    parts[1]["annotations"] = None
    assert len(completed_answer(raw, scope)["citations"]) == 1
    assert "overlaps" in caplog.text
    assert "not an object" in caplog.text
    assert "not a list" in caplog.text
    for part in parts:
        part.pop("annotations")
    assert completed_answer(raw, scope)["citations"] == []


def test_retained_answer_restores_sources_without_inference_and_checks_scope(tmp_path, monkeypatch):
    raw, _, _ = citation_response()
    cases = fixture_cases()
    folder = tmp_path / ".local" / "workbench"
    folder.mkdir(parents=True)
    (folder / "resp_citations.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr("agents.workbench.ROOT", tmp_path)
    inference = MagicMock(side_effect=AssertionError("Must not start inference"))
    monkeypatch.setattr("agents.workbench.ask_foundry", inference)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler(None, cases)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/presenter/response"
        try:
            with urlopen(base + "?responseId=resp_citations&siteId=SE-STO-0042") as result:
                answer = json.load(result)
                assert len(answer["citations"]) == 2
                assert result.headers["Cache-Control"] == "no-store"
            for query, code in [
                ("responseId=resp_citations&siteId=SE-GOT-0017", 400),
                ("responseId=resp_missing&siteId=SE-STO-0042", 404),
                ("responseId=..%2F..%2Fseed&siteId=SE-STO-0042", 400),
                ("responseId=resp_citations&responseId=resp_other&siteId=SE-STO-0042", 400),
                ("responseId=resp_citations&siteId=SE-STO-0042&url=https://other.test", 400),
            ]:
                with pytest.raises(HTTPError) as error:
                    urlopen(base + "?" + query)
                assert error.value.code == code
                error.value.close()
            request = Request(
                base + "?responseId=resp_citations&siteId=SE-STO-0042",
                headers={"Origin": "https://untrusted.example.test"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(request)
            assert error.value.code == 403
            error.value.close()
            raw["status"] = "failed"
            (folder / "resp_citations.json").write_text(json.dumps(raw), encoding="utf-8")
            with pytest.raises(HTTPError) as error:
                urlopen(base + "?responseId=resp_citations&siteId=SE-STO-0042")
            assert error.value.code == 400
            error.value.close()
            inference.assert_not_called()
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("site", ["SE-STO-0042", "SE-GOT-0017"])
def test_scoped_answer_requires_actual_fixture_provenance(site):
    raw, scope = scoped_response(site)
    assert completed_answer(raw, scope)["responseId"] == raw["id"]


@pytest.mark.parametrize("key", ["siteId", "caseId", "program", "scenario"])
def test_scoped_answer_rejects_missing_forwarded_scope(key):
    raw, scope = scoped_response()
    raw["output"][-1]["arguments"] = json.dumps(
        {"userQuestion": " ".join(value for name, value in scope.items() if name != key)}
    )
    with pytest.raises(AssistantFailure, match="omitted"):
        completed_answer(raw, scope)


@pytest.mark.parametrize(
    "result",
    [
        '{"status":"failed","error":{"code":"KustoInternalServerError"}}',
        "PBIServiceException: unable to retrieve facts",
        "fabric:contract-v01:fallback-ready:SE-GOT-0017:" + "a" * 64,
        "fabric:contract-v01:initial-blocked:SE-STO-0042:missing-hash",
        None,
    ],
)
def test_completed_tool_cannot_hide_failed_or_wrong_fixture_retrieval(result):
    raw, scope = scoped_response()
    raw["output"][-1]["output"] = result
    with pytest.raises(AssistantFailure, match="verifiable facts|both IQ"):
        completed_answer(raw, scope)


@pytest.mark.parametrize("arguments", ["invalid-json", "null", "[]", "{}"])
def test_scoped_answer_rejects_invalid_tool_arguments(arguments):
    raw, scope = scoped_response()
    raw["output"][-1]["arguments"] = arguments
    with pytest.raises(AssistantFailure, match="scope|omitted"):
        completed_answer(raw, scope)


@pytest.mark.parametrize("key", ["caseId", "program"])
def test_fixture_reference_alone_does_not_verify_the_returned_case_and_program(key):
    raw, scope = scoped_response()
    raw["output"][-1]["output"] = raw["output"][-1]["output"].replace(scope[key], "")
    with pytest.raises(AssistantFailure, match="verifiable facts"):
        completed_answer(raw, scope)


CONNECTION_SOURCE = (
    "/subscriptions/demo/resourceGroups/demo/providers/Microsoft.CognitiveServices/"
    "accounts/demo/projects/demo/connections/coordinator-fabric"
)
CONNECTION_TARGET = "https://api.fabric.microsoft.com/v1/mcp/workspaces/demo/dataagents/demo/agent"


def delegated_properties():
    return {
        "category": "RemoteTool",
        "authType": "UserEntraToken",
        "target": CONNECTION_TARGET,
        "audience": DELEGATED_AUDIENCE,
        "scopes": [f"{DELEGATED_AUDIENCE}/DataAgent.Execute.All"],
    }


def test_delegated_connection_reuses_matching_connection_without_writes():
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"properties": delegated_properties()})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert (
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET
            )
            == "assistant-fabric"
        )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith("/connections/assistant-fabric")


@pytest.mark.parametrize("create", [False, True])
def test_delegated_connection_creation_is_explicit_and_verified(create):
    requests = []

    def transport(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={"properties": delegated_properties()})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        if not create:
            with pytest.raises(ValueError, match="authorize"):
                reconcile_delegated_connection(
                    client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET
                )
            assert len(requests) == 1
        else:
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET, create=True
            )
            assert [row.method for row in requests] == ["GET", "PUT", "GET"]
            assert requests[1].headers["If-None-Match"] == "*"
            body = json.loads(requests[1].content)["properties"]
            assert body["authType"] == "UserEntraToken"
            assert body["scopes"] == delegated_properties()["scopes"]
            assert "credentials" not in body
            assert all("/connections/assistant-fabric" in row.url.path for row in requests)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("authType", "ProjectManagedIdentity"),
        ("target", "https://example.com/another-agent"),
        ("audience", "https://api.fabric.microsoft.com"),
        ("scopes", ["another/scope"]),
        ("category", "unrelated"),
    ],
)
def test_delegated_connection_never_overwrites_a_mismatch(key, value):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"properties": {**delegated_properties(), key: value}})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match="different"):
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET, create=True
            )
    assert [row.method for row in requests] == ["GET"]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_delegated_connection_read_errors_do_not_create(status):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET, create=True
            )
    assert [row.method for row in requests] == ["GET"]


@pytest.mark.parametrize("failure", ["concurrent-create", "readback-mismatch"])
def test_delegated_creation_requires_no_race_and_matching_readback(failure):
    requests = []

    def transport(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(412 if failure == "concurrent-create" else 200)
        if len(requests) == 1:
            return httpx.Response(404)
        return httpx.Response(
            200, json={"properties": {**delegated_properties(), "authType": "Anonymous"}}
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        expected = httpx.HTTPStatusError if failure == "concurrent-create" else ValueError
        with pytest.raises(expected):
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, "assistant-fabric", CONNECTION_TARGET, create=True
            )
    assert sum(row.method == "PUT" for row in requests) == 1


@pytest.mark.parametrize("name", ["coordinator-fabric", "COORDINATOR-FABRIC", "../another"])
def test_assistant_connection_cannot_replace_the_source(name):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("No network call expected"))
    ) as client:
        with pytest.raises(ValueError, match="separate assistant"):
            reconcile_delegated_connection(
                client, CONNECTION_SOURCE, name, CONNECTION_TARGET, create=True
            )


@pytest.mark.parametrize(
    ("host", "origin", "expected"),
    [
        ("127.0.0.1:5190", None, True),
        ("127.0.0.1:5190", "http://127.0.0.1:5187", True),
        ("localhost:5190", "http://localhost:5173", True),
        ("127.0.0.1:5190", "https://evil.example", False),
        ("evil.example:5190", None, False),
        ("127.0.0.1:5190", "null", False),
    ],
)
def test_operator_api_restricts_host_and_origin(host, origin, expected):
    assert allowed_request(host, origin, 5190) is expected


def test_loopback_api_has_no_actions_or_silent_chat_fallback():
    with ThreadingHTTPServer(("127.0.0.1", 0), handler(None, [])) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/presenter/status") as result:
                assert json.load(result)["configured"] is False
            request = Request(
                base + "/api/presenter/chat",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(request)
            assert error.value.code == 503
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/api/presenter/approve")
            assert error.value.code == 404
            request = Request(
                base + "/api/presenter/status", headers={"Origin": "https://evil.example"}
            )
            with pytest.raises(HTTPError) as error:
                urlopen(request)
            assert error.value.code == 403
        finally:
            server.shutdown()
            thread.join()


def test_validation_api_exposes_only_configured_input_and_durable_submissions():
    validation = MagicMock()
    validation.info.return_value = {"configured": True, "evidenceId": "PHOTO-20"}
    validation.photo.return_value = b"pinned PNG bytes"
    validation.start.return_value = {"status": "Running", "responseId": "resp-test"}
    validation.poll.return_value = {"status": "Completed"}
    identifier = str(uuid4())
    with ThreadingHTTPServer(("127.0.0.1", 0), handler(None, [], validation)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/presenter/validation"
        try:
            with urlopen(base) as result:
                assert json.load(result)["evidenceId"] == "PHOTO-20"
            with urlopen(base + "/photo") as result:
                assert result.headers["Content-Type"] == "image/png"
                assert result.headers["Cache-Control"] == "no-store"
                assert result.read() == b"pinned PNG bytes"
            with urlopen(
                Request(
                    base,
                    data=json.dumps(
                        {
                            "siteId": "SE-STO-0042",
                            "evidenceId": "PHOTO-20",
                            "publicationId": identifier,
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
            ) as result:
                assert result.status == 202
                assert json.load(result)["responseId"] == "resp-test"
            validation.start.assert_called_once()
            with urlopen(base + f"/run?publicationId={identifier}") as result:
                assert json.load(result)["status"] == "Completed"
            validation.poll.assert_called_once_with(identifier)
            for suffix in ("/run", "/arbitrary-url?url=https://evil.example"):
                with pytest.raises(HTTPError) as error:
                    urlopen(base + suffix)
                assert error.value.code in {400, 404}
            with pytest.raises(HTTPError) as error:
                urlopen(
                    Request(
                        base,
                        data=b'{"url":"https://evil.example"}',
                        headers={"Content-Type": "application/json"},
                    )
                )
            assert error.value.code == 400
            validation.start.assert_called_once()
        finally:
            server.shutdown()
            thread.join()
