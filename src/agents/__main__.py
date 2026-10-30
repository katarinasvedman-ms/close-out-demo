"""Register and run real Foundry agents with explicitly synthetic, scoped dependencies."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from agents.definitions import FabricSettings, FoundrySettings, KnowledgeSettings, PublisherSettings
from agents.foundry import invoke, register
from agents.sources import Sources, mcp_facts
from shared.local_demo import write_json
from shared.seed import load_seed


async def sources(args, site: str, scenario: str, overlay: Path | None = None) -> Sources:
    facts = await mcp_facts(site, scenario, args.seed, args.contracts, overlay)
    return Sources(facts, args.assets, args.contracts)


async def run(args) -> None:
    settings = FoundrySettings.model_validate_json(args.config.read_text(encoding="utf-8"))
    if settings.fabric:
        if args.command != "gaps" or args.validation_results or not args.program:
            raise ValueError(
                "Native Fabric fixtures support gaps --program only; no local overlays or smoke"
            )
        if args.site not in settings.knowledge.sites:
            raise PermissionError("Site is not configured for the shared synthetic knowledge demo")
        from agents.fabric import read_facts

        facts = await asyncio.to_thread(
            read_facts, settings.fabric, args.site, args.program, args.scenario, args.contracts
        )
        inputs = Sources(facts, args.assets, args.contracts)
    else:
        inputs = None
    if args.command == "smoke":
        await smoke(args, settings)
        return
    if inputs is None:
        inputs = await sources(args, args.site, args.scenario, args.validation_results)
    kind = "validation" if args.command == "validate" else "closeout"
    receipt = await invoke(kind, settings, inputs, getattr(args, "evidence", None))
    target = args.output / f"{args.command}-{receipt['correlationId']}.json"
    write_json(target, receipt)
    print(json.dumps({"receipt": str(target), "result": receipt["result"]}, indent=2))


async def smoke(args, settings: FoundrySettings) -> None:
    base = load_seed(args.seed, args.contracts)
    spec = base.recipe["scenario"]
    target = args.output / f"smoke-{uuid4().hex}"
    initial = await sources(args, spec["mainSiteId"], "replacements-reviewed")
    originals = [row["replacesPhotoId"] for row in spec["replacements"]]
    replacements = [row["photoId"] for row in spec["replacements"]]
    rfds = next(a["artifactId"] for a in initial.facts["artifacts"] if a["artifactType"] == "rfds")
    validation = await invoke("validation", settings, initial, [*originals, *replacements, rfds])
    write_json(target / "validation.json", validation)
    by_id = {row.get("photoId", row.get("artifactId")): row for row in validation["result"]}
    expected = {**dict.fromkeys(originals, False), **dict.fromkeys([*replacements, rfds], True)}
    for identifier, compliant in expected.items():
        if by_id[identifier]["compliant"] != compliant:
            raise ValueError(f"Live validation smoke expectation failed: {identifier}")
        if compliant and by_id[identifier]["reviewStatus"] != "Auto":
            raise ValueError(f"Live confidence requires review: {identifier}")
    overlay = target / "photo-validation-results.json"
    write_json(overlay, [row for row in validation["result"] if "photoId" in row])
    blocked = await sources(args, spec["mainSiteId"], "initial-blocked", overlay)
    gap_receipt = await invoke("closeout", settings, blocked)
    write_json(target / "blocked-gap-analysis.json", gap_receipt)
    if blocked.facts["readiness"] != "Blocked":
        raise ValueError("Live original findings did not preserve the blocked fixture")
    corrected = await sources(args, spec["mainSiteId"], "replacements-reviewed", overlay)
    if corrected.facts["readiness"] != "Ready":
        raise ValueError("Live replacement findings did not satisfy the corrected fixture")
    fallback = await sources(args, spec["fallbackSiteId"], "fallback-ready")
    ready_receipt = await invoke("closeout", settings, fallback)
    write_json(target / "ready-gap-analysis.json", ready_receipt)
    if ready_receipt["result"]["gaps"] or fallback.facts["readiness"] != "Ready":
        raise ValueError("The Ready fallback acquired fabricated gaps")
    report = {
        "passed": True,
        "validationExpectations": expected,
        "model": settings.model,
        "modelVersion": settings.modelVersion,
        "validationAgent": settings.validation.model_dump(),
        "closeoutAgent": settings.closeout.model_dump(),
        "blockedConditions": blocked.facts["readinessMissing"],
        "correctedReadiness": corrected.facts["readiness"],
        "fallbackReadiness": fallback.facts["readiness"],
        "responseIds": [
            validation["responseId"],
            gap_receipt["responseId"],
            ready_receipt["responseId"],
        ],
        "limitations": [
            "Synthetic schematics/PDFs; not a field-evidence quality evaluation",
            "Readiness, ticket closure and rework closure are fixture scenarios, not real actions",
            "No Dataverse, Content Understanding, package rendering or live Fabric",
            (
                "Live Foundry IQ over synthetic document text counterparts, not PDF OCR"
                if settings.knowledge
                else "No Foundry IQ; hash-checked local synthetic documents"
            ),
        ],
    }
    write_json(target / "report.json", report)
    print(json.dumps({"report": str(target / "report.json"), **report}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("register", help="Create/reuse immutable Foundry agent versions")
    deploy.add_argument("--endpoint", required=True)
    deploy.add_argument("--deployment", required=True)
    deploy.add_argument("--model", required=True)
    deploy.add_argument("--model-version", required=True)
    deploy.add_argument("--validation-name", default="close-out-validation")
    deploy.add_argument("--closeout-name", default="close-out-coordinator")
    deploy.add_argument("--config", type=Path, default=Path(".local") / "foundry" / "config.json")
    deploy.add_argument("--knowledge-config", type=Path)
    deploy.add_argument("--fabric-config", type=Path)
    deploy.add_argument("--publisher-config", type=Path)
    for command in ("validate", "gaps", "smoke"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", type=Path, default=Path(".local") / "foundry" / "config.json")
        sub.add_argument("--seed", type=Path, default=Path("seed") / "seed.json")
        sub.add_argument("--contracts", type=Path, default=Path("contracts"))
        sub.add_argument("--assets", type=Path, default=Path(".local") / "demo")
        sub.add_argument("--output", type=Path, default=Path(".local") / "foundry" / "results")
        if command != "smoke":
            sub.add_argument("--site", required=True)
            sub.add_argument("--scenario", default="initial-blocked")
            sub.add_argument("--program")
            sub.add_argument("--validation-results", type=Path)
        if command == "validate":
            sub.add_argument("--evidence", action="append", required=True)
    args = parser.parse_args()
    if args.command == "register":
        settings = register(
            args.endpoint,
            args.deployment,
            args.model,
            args.model_version,
            args.config,
            args.validation_name,
            args.closeout_name,
            (
                KnowledgeSettings.model_validate_json(
                    args.knowledge_config.read_text(encoding="utf-8")
                )
                if args.knowledge_config
                else None
            ),
            (
                FabricSettings.model_validate_json(args.fabric_config.read_text(encoding="utf-8"))
                if args.fabric_config
                else None
            ),
            (
                PublisherSettings.model_validate_json(
                    args.publisher_config.read_text(encoding="utf-8")
                )
                if args.publisher_config
                else None
            ),
        )
        print(settings.model_dump_json(indent=2))
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
