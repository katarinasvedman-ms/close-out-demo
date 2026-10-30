"""Loopback-only operator demo API. Not a deployed process owner or coordinator runner."""

import argparse
import json
import logging
import re
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents.assistant import AssistantSettings
from agents.definitions import definition_hash
from agents.flow import storage_endpoint
from agents.workbench_briefs import BriefReader
from agents.workbench_validation import ValidationRequest, ValidationRunner, ValidationSettings
from shared.data_agent_stub import DataAgentStub
from shared.local_demo import write_json
from shared.seed import load_seed

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MAX_BODY = 24_000


class AssistantFailure(RuntimeError):
    """A retained response or pinned agent cannot support a grounded answer."""


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    siteId: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)


def fixture_cases() -> list[dict]:
    base = load_seed(ROOT / "seed" / "seed.json", ROOT / "contracts")
    sites = [base.recipe["scenario"]["mainSiteId"], base.recipe["scenario"]["fallbackSiteId"]]
    stub = DataAgentStub(base, set(sites))
    return [
        stub.site_facts(site, "initial-blocked" if index == 0 else "fallback-ready")
        for index, site in enumerate(sites)
    ]


def site_scope(site_id: str, cases: list[dict]) -> dict:
    case = next((row for row in cases if row["site"]["siteId"] == site_id), None)
    if case is None:
        raise ValueError("Select one of the configured demonstration sites")
    return {
        "siteId": site_id,
        "caseId": case["caseId"],
        "program": case["project"]["programCode"],
        "scenario": case["scenario"],
        "sourceMode": "synthetic-fabric-fixture",
    }


def chat_scope(request: ChatRequest, cases: list[dict]) -> dict:
    scope = site_scope(request.siteId, cases)
    if request.messages[-1].role != "user":
        raise ValueError("A chat request must end with a user question")
    return scope


def document_excerpts(output: list[dict]) -> dict[str, str]:
    excerpts: dict[str, list[str]] = {}
    for item in output:
        if item.get("type") != "mcp_call" or item.get("server_label") != "documents":
            continue
        try:
            result = json.loads(item.get("output", ""))
        except (ValueError, TypeError):
            LOG.warning("Document retrieval has no structured excerpts")
            continue
        if not isinstance(result, dict) or not isinstance(result.get("documents"), list):
            LOG.warning("Document retrieval has an unsupported excerpt shape")
            continue
        for document in result["documents"]:
            try:
                if not isinstance(document, dict):
                    raise ValueError("Invalid document")
                content = json.loads(document.get("content", ""))
                url = document.get("url")
                if not isinstance(content, dict) or not isinstance(url, str):
                    raise ValueError("Missing document URL or content")
                parsed = urlsplit(url)
                snippet = content.get("snippet")
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or url != content.get("blob_url")
                    or not isinstance(snippet, str)
                    or not snippet.strip()
                ):
                    raise ValueError("Invalid document provenance or excerpt")
            except (ValueError, TypeError):
                LOG.warning("Document retrieval contains an unavailable excerpt")
                continue
            chunks = excerpts.setdefault(url, [])
            if snippet not in chunks:
                chunks.append(snippet)
    return {url: "\n\n".join(chunks) for url, chunks in excerpts.items()}


def answer_citations(parts: list[dict], excerpts: dict[str, str]) -> list[dict]:
    citations = []
    offset = 0
    for part in parts:
        annotations = part.get("annotations", [])
        if not isinstance(annotations, list):
            LOG.warning("Assistant citation annotations are not a list")
            annotations = []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                LOG.warning("Assistant citation annotation is not an object")
                continue
            if annotation.get("type") != "url_citation":
                continue
            start, end = annotation.get("start_index"), annotation.get("end_index")
            url = annotation.get("url")
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(part["text"])
                or not isinstance(url, str)
                or url not in excerpts
            ):
                LOG.warning("Assistant citation has no matching document excerpt or valid span")
                continue
            citations.append(
                {
                    "start": offset + start,
                    "end": offset + end,
                    "url": url,
                    "title": urlsplit(url).path.rsplit("/", 1)[-1] or "Document excerpt",
                    "excerpt": excerpts[url],
                }
            )
        offset += len(part["text"]) + 1
    ordered = []
    for citation in sorted(citations, key=lambda row: (row["start"], row["end"])):
        if ordered and citation["start"] < ordered[-1]["end"]:
            LOG.warning("Assistant citation overlaps an earlier citation")
            continue
        ordered.append(citation)
    return ordered


def completed_answer(response: dict, scope: dict | None = None) -> dict:
    if response.get("status") != "completed":
        error = response.get("error") or {}
        raise AssistantFailure(
            f"Foundry response {response.get('id')} did not complete "
            f"({response.get('status')}; {error.get('code', 'no error code')}): "
            f"{error.get('message', 'No completed answer was returned.')}"
        )
    output = response.get("output", [])
    failures = [
        item
        for item in output
        if item.get("type") == "mcp_call" and (item.get("error") or item.get("status") == "failed")
    ]
    if failures:
        labels = sorted({item.get("server_label", "unknown") for item in failures})
        raise AssistantFailure(f"Native retrieval failed: {', '.join(labels)}")
    messages = [item for item in output if item.get("type") == "message"]
    parts = [
        part
        for message in messages
        if message.get("role") == "assistant"
        for part in message.get("content", [])
        if part.get("type") == "output_text"
    ]
    text = "\n".join(part["text"] for part in parts)
    if not text.strip():
        raise AssistantFailure("Foundry returned no final assistant text")
    tools = [
        {
            "source": item["server_label"],
            "name": item["name"],
            "callId": item["id"],
        }
        for item in output
        if item.get("type") == "mcp_call"
        and item.get("server_label") in {"documents", "fabric-facts"}
        and item.get("output")
        and not item.get("error")
    ]
    if {tool["source"] for tool in tools} != {"documents", "fabric-facts"}:
        raise AssistantFailure(
            f"Foundry response {response['id']} lacks successful retrieval from both IQ sources. "
            "No ungrounded answer is displayed."
        )
    if scope is not None:
        for item in output:
            if item.get("type") != "mcp_call" or item.get("server_label") != "fabric-facts":
                continue
            try:
                arguments = json.loads(item.get("arguments", ""))
            except (TypeError, ValueError) as error:
                raise AssistantFailure("Fabric retrieval has invalid scope arguments") from error
            question = arguments.get("userQuestion") if isinstance(arguments, dict) else None
            if not isinstance(question, str) or any(
                scope[key] not in question for key in ("siteId", "caseId", "program", "scenario")
            ):
                raise AssistantFailure(
                    "Fabric retrieval omitted the selected site, case, program or scenario. "
                    f"No ungrounded answer is displayed (response {response['id']})."
                )
            result = item.get("output")
            reference = (
                re.escape(f"fabric:contract-v01:{scope['scenario']}:{scope['siteId']}:")
                + r"[a-f0-9]{64}(?![a-f0-9])"
            )
            if (
                not isinstance(result, str)
                or not re.search(reference, result)
                or any(scope[key] not in result for key in ("caseId", "program"))
            ):
                raise AssistantFailure(
                    "Fabric returned no verifiable facts for the selected fixture. "
                    "The tool may have reported an internal retrieval error; "
                    f"no ungrounded answer is displayed (response {response['id']})."
                )
    return {
        "text": text,
        "responseId": response["id"],
        "retrieval": tools,
        "citations": answer_citations(parts, document_excerpts(output)),
    }


def ask_foundry(settings: AssistantSettings, scope: dict, request: ChatRequest) -> dict:
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=settings.endpoint, credential=credential) as project,
        project.get_openai_client(timeout=45, max_retries=0) as client,
    ):
        agent = project.agents.get_version(
            agent_name=settings.agent.name, agent_version=settings.agent.version
        )
        if definition_hash(agent.definition.as_dict()) != settings.agent.definitionHash:
            # Normalize the service's allowed_tools representation before hashing.
            body = agent.definition.as_dict()
            for tool in body.get("tools", []):
                if tool.get("type") == "mcp" and isinstance(tool.get("allowed_tools"), dict):
                    tool["allowed_tools"] = tool["allowed_tools"]["tool_names"]
            if definition_hash(body) != settings.agent.definitionHash:
                raise AssistantFailure("Pinned assistant definition changed; register it again")
        response = client.responses.create(
            input=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "scope": scope,
                            "conversation": [row.model_dump() for row in request.messages],
                        }
                    ),
                }
            ],
            background=True,
            extra_body={
                "agent_reference": {
                    "type": "agent_reference",
                    "name": settings.agent.name,
                    "version": settings.agent.version,
                }
            },
        )
        deadline = time.monotonic() + 240
        while response.status in {"queued", "in_progress"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Foundry response {response.id} exceeded the four-minute wait; "
                    "it may still be running. Do not blindly retry."
                )
            time.sleep(2)
            response = client.responses.retrieve(response.id)
        raw = response.model_dump(mode="json")
        write_json(ROOT / ".local" / "workbench" / f"{response.id}.json", raw)
        return completed_answer(raw, scope)


def allowed_request(host: str | None, origin: str | None, port: int) -> bool:
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return False
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def handler(
    settings: AssistantSettings | None,
    cases: list[dict],
    validation: ValidationRunner | None = None,
    briefs: BriefReader | None = None,
):
    busy = BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, value: dict | list) -> None:
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def admitted(self) -> bool:
            if not allowed_request(
                self.headers.get("Host"), self.headers.get("Origin"), self.server.server_port
            ):
                self.send_json(403, {"error": "Only local operator requests are accepted"})
                return False
            return True

        def request_body(self) -> bytes:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError("Request body must be between 1 and 24000 bytes")
            return self.rfile.read(size)

        def do_GET(self) -> None:
            if not self.admitted():
                return
            url = urlsplit(self.path)
            if url.path in {"/api/presenter/briefs", "/api/presenter/brief"}:
                if briefs is None:
                    self.send_json(503, {"error": "Saved briefs are not configured"})
                    return
                try:
                    query = parse_qs(url.query, keep_blank_values=True)
                    expected = {"siteId", "briefId"} if url.path.endswith("/brief") else {"siteId"}
                    if set(query) != expected or any(len(values) != 1 for values in query.values()):
                        raise ValueError("Select one site and, when reading, one brief")
                    scope = site_scope(query["siteId"][0], cases)
                    value = (
                        briefs.read(query["briefId"][0], scope)
                        if "briefId" in query
                        else briefs.list(scope)
                    )
                    self.send_json(200, value)
                except FileNotFoundError:
                    self.send_json(404, {"error": "The saved brief is unavailable"})
                except ValueError as error:
                    LOG.warning("Rejected brief request: %s", error)
                    self.send_json(400, {"error": "Invalid brief request or unsupported site"})
                except Exception:
                    LOG.exception("Could not read saved coordinator briefs")
                    self.send_json(
                        502, {"error": "Could not read saved briefs; refresh to try again"}
                    )
                return
            if url.path == "/api/presenter/response":
                try:
                    query = parse_qs(url.query, keep_blank_values=True)
                    if set(query) != {"responseId", "siteId"} or any(
                        len(values) != 1 for values in query.values()
                    ):
                        raise ValueError("One response ID and site ID are required")
                    identifier = query["responseId"][0]
                    if not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,128}", identifier):
                        raise ValueError("Invalid response ID")
                    scope = site_scope(query["siteId"][0], cases)
                    path = ROOT / ".local" / "workbench" / f"{identifier}.json"
                    response = json.loads(path.read_text(encoding="utf-8"))
                    self.send_json(200, completed_answer(response, scope))
                except FileNotFoundError:
                    self.send_json(404, {"error": "The retained response is unavailable"})
                except (ValueError, AssistantFailure) as error:
                    LOG.warning("Rejected retained response: %s", error)
                    self.send_json(400, {"error": str(error)})
                except OSError:
                    LOG.exception("Could not read the retained response")
                    self.send_json(502, {"error": "Could not read the retained response"})
                return
            if url.path in {
                "/api/presenter/validation",
                "/api/presenter/validation/run",
                "/api/presenter/validation/photo",
            }:
                if validation is None:
                    if url.path == "/api/presenter/validation":
                        self.send_json(200, {"configured": False})
                    else:
                        self.send_json(
                            503, {"error": "No registered validation target is configured"}
                        )
                    return
                try:
                    if url.path == "/api/presenter/validation":
                        self.send_json(200, validation.info())
                    elif url.path == "/api/presenter/validation/run":
                        identifiers = parse_qs(url.query).get("publicationId", [])
                        if len(identifiers) != 1:
                            raise ValueError("One publication ID is required")
                        self.send_json(200, validation.poll(identifiers[0]))
                    elif url.path == "/api/presenter/validation/photo":
                        data = validation.photo()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        self.wfile.write(data)
                except ValueError as error:
                    LOG.warning("Rejected validation request: %s", error)
                    self.send_json(400, {"error": str(error)})
                except Exception:
                    LOG.exception("Validation status or evidence read failed")
                    self.send_json(
                        502, {"error": "Could not read validation state/evidence. Check API log."}
                    )
                return
            if self.path == "/api/presenter/cases":
                self.send_json(200, cases)
            elif self.path == "/api/presenter/status":
                self.send_json(
                    200,
                    {
                        "configured": settings is not None,
                        "agent": settings.agent.model_dump() if settings else None,
                        "caseSource": "local-seed-fixture",
                        "assistantSource": "live-foundry" if settings else "not-configured",
                    },
                )
            else:
                self.send_json(404, {"error": "Unknown presenter endpoint"})

        def do_POST(self) -> None:
            if not self.admitted():
                return
            if self.path == "/api/presenter/validation":
                if validation is None:
                    self.send_json(503, {"error": "No registered validation target is configured"})
                    return
                try:
                    request = ValidationRequest.model_validate_json(self.request_body())
                    self.send_json(202, validation.start(request))
                except ValueError as error:
                    LOG.warning("Rejected validation submission: %s", error)
                    self.send_json(400, {"error": str(error)})
                except Exception:
                    LOG.exception("Validation submission could not be acknowledged")
                    self.send_json(
                        502,
                        {
                            "error": (
                                "Submission outcome could not be read. "
                                "Refresh status; do not resubmit."
                            )
                        },
                    )
                return
            if self.path != "/api/presenter/chat":
                self.send_json(404, {"error": "Unknown presenter endpoint"})
                return
            if settings is None:
                self.send_json(
                    503, {"error": "No Foundry assistant is configured; no stub fallback"}
                )
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json(415, {"error": "Expected application/json"})
                return
            try:
                request = ChatRequest.model_validate_json(self.request_body())
                scope = chat_scope(request, cases)
            except (ValueError, ValidationError) as error:
                LOG.warning("Rejected presenter chat: %s", error)
                self.send_json(400, {"error": "Invalid chat request or unsupported site"})
                return
            if not busy.acquire(blocking=False):
                self.send_json(429, {"error": "An assistant request is already running"})
                return
            try:
                answer = ask_foundry(settings, scope, request)
                self.send_json(200, answer)
            except TimeoutError as error:
                LOG.exception("Assistant response timeout")
                self.send_json(504, {"error": str(error)})
            except AssistantFailure as error:
                LOG.exception("Assistant could not ground an answer")
                self.send_json(502, {"error": str(error)})
            except Exception:
                LOG.exception("Foundry assistant request failed")
                self.send_json(
                    502,
                    {"error": "Foundry assistant failed. Check the local API log; no fallback."},
                )
            finally:
                busy.release()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(".local") / "foundry" / "assistant.json"
    )
    parser.add_argument("--port", type=int, default=5190)
    parser.add_argument(
        "--briefs-storage-url",
        help="Existing flow Blob endpoint; defaults to the registered validation target",
    )
    parser.add_argument(
        "--validation-config", type=Path, default=Path(".local") / "foundry" / "validation.json"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = (
        AssistantSettings.model_validate_json(args.config.read_text(encoding="utf-8"))
        if args.config.exists()
        else None
    )
    if settings is None:
        LOG.warning("Assistant config absent; only labelled fixture views are available")
    validation = (
        ValidationRunner(
            ValidationSettings.model_validate_json(
                args.validation_config.read_text(encoding="utf-8")
            ),
            ROOT / ".local" / "workbench",
        )
        if args.validation_config.exists()
        else None
    )
    if validation is None:
        LOG.warning("Validation config absent; submission is unavailable")
    storage_url = args.briefs_storage_url or (
        validation.settings.storageUrl if validation is not None else None
    )
    with ExitStack() as stack:
        briefs = None
        if storage_url is not None:
            from azure.identity import AzureCliCredential
            from azure.storage.blob import ContainerClient

            credential = stack.enter_context(AzureCliCredential())
            container = stack.enter_context(
                ContainerClient(storage_endpoint(storage_url), "results", credential=credential)
            )
            briefs = BriefReader(container)
        else:
            LOG.warning("Brief storage is not configured; no local substitute is used")
        routes = handler(settings, fixture_cases(), validation, briefs)
        server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", args.port), routes))
        LOG.info("Local workbench API listening on http://127.0.0.1:%s", args.port)
        server.serve_forever()


if __name__ == "__main__":
    main()
