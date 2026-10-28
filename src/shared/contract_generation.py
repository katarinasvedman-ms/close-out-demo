"""Generate deterministic reader models and self-contained producer OpenAPI."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from shared.contract_validation import bundle_schemas, load_schemas


def codegen_projection(value):
    if isinstance(value, list):
        return [codegen_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: codegen_projection(item) for key, item in value.items()}
    if "allOf" in result and "properties" in result:
        annotations = {
            key: result.pop(key)
            for key in ("title", "description", "$defs", "$schema")
            if key in result
        }
        branches = result.pop("allOf")
        return {**annotations, "allOf": [*branches, result]}
    if "oneOf" in result and "properties" in result:
        annotations = {key: result.pop(key) for key in ("title", "description") if key in result}
        branches = result.pop("oneOf")
        return {**annotations, "allOf": [result, {"oneOf": branches}]}
    return result


def bundled_openapi(root: Path) -> dict:
    spec = yaml.safe_load((root / "apim" / "openapi.yaml").read_text(encoding="utf-8"))
    bundle = bundle_schemas(root)
    targets = {}
    for schema in load_schemas(root).values():
        targets[schema["$id"]] = schema["title"]
        for name in schema.get("$defs", {}):
            targets[schema["$id"] + "#/$defs/" + name] = name
        if schema["$id"] == "urn:close-out-demo:contracts:codes":
            for name in schema["properties"]:
                targets[schema["$id"] + "#/properties/" + name] = name

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "$ref":
                if item.startswith("#/$defs/"):
                    item = "#/components/schemas/" + item.removeprefix("#/$defs/")
                elif not item.startswith("#/"):
                    item = "#/components/schemas/" + targets[item]
                result[key] = item
            else:
                result[key] = rewrite(item)
        return result

    spec["components"]["schemas"] = bundle["$defs"]
    return rewrite(spec)


def generate(repository: Path, *, check: bool = False) -> None:
    contracts = repository / "contracts"
    destination = repository / "src" / "shared" / "contracts"
    with tempfile.TemporaryDirectory(prefix="close-out-contracts-") as directory:
        temp = Path(directory)
        bundle = temp / "bundle.json"
        bundle.write_text(
            json.dumps(codegen_projection(bundle_schemas(contracts, tolerant=True))),
            encoding="utf-8",
        )
        python = temp / "models.py"
        typescript = temp / "models.ts"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(bundle),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(python),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.11",
                "--disable-timestamp",
                "--use-standard-collections",
                "--use-union-operator",
                "--use-title-as-name",
                "--field-constraints",
                "--extra-fields",
                "ignore",
            ],
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--fix", "--ignore", "E501", str(python)],
            check=True,
        )
        subprocess.run([sys.executable, "-m", "ruff", "format", str(python)], check=True)
        subprocess.run(
            [
                "node",
                str(repository / "scripts" / "generate-types.mjs"),
                str(bundle),
                str(typescript),
            ],
            check=True,
            cwd=repository,
        )
        outputs = {
            "models.py": python.read_text(encoding="utf-8").replace(
                "#   filename:  bundle.json", "#   filename:  contracts (reader projection)"
            ),
            "models.ts": typescript.read_text(encoding="utf-8"),
            "openapi.json": json.dumps(bundled_openapi(contracts), indent=2) + "\n",
        }
        for name, content in outputs.items():
            target = destination / name
            if check:
                if not target.exists() or target.read_text(encoding="utf-8") != content:
                    relative = target.relative_to(repository)
                    raise ValueError(f"Generated contract is stale: {relative}")
            else:
                destination.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
    print("Generated contracts are current" if check else "Generated contract types and OpenAPI")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(Path.cwd(), check=args.check)


if __name__ == "__main__":
    main()
