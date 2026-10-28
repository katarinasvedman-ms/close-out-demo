"""Validate local contract schemas and their declared examples without remote retrieval."""

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urldefrag

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def _local_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Contract path escapes its root: {relative}")
    return path


def json_pointer(document, pointer: str):
    if not pointer:
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"Only JSON Pointer fragments are supported: {pointer}")
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


def reader_schema(value):
    if isinstance(value, list):
        return [reader_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: reader_schema(item) for key, item in value.items() if key != "enum"}
    return result


def load_schemas(root: Path) -> dict[str, dict]:
    schemas = {}
    ids = set()
    for path in sorted(root.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schema_id = schema["$id"]
        if schema_id in ids:
            raise ValueError(f"Duplicate schema ID: {schema_id}")
        ids.add(schema_id)
        schemas[path.relative_to(root).as_posix()] = schema
    if not schemas:
        raise ValueError(f"No contract schemas found in {root}")
    return schemas


def load_validators(root: Path, *, tolerant: bool = False) -> dict[str, Draft202012Validator]:
    schemas = load_schemas(root)
    if tolerant:
        schemas = {name: reader_schema(schema) for name, schema in schemas.items()}
    resources = {schema["$id"]: Resource.from_contents(schema) for schema in schemas.values()}
    registry = Registry().with_resources(resources.items())
    return {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }


def select_validator(validators, reference: str) -> Draft202012Validator:
    name, fragment = urldefrag(reference)
    validator = validators[name]
    if fragment:
        json_pointer(validator.schema, fragment)
        return validator.evolve(schema={"$ref": validator.schema["$id"] + "#" + fragment})
    return validator


def example_payload(root: Path, reference: str):
    name, fragment = urldefrag(reference)
    path = _local_path(root, name)
    return json_pointer(json.loads(path.read_text(encoding="utf-8")), fragment)


def validate_examples(root: Path) -> int:
    root = root.resolve()
    validators = load_validators(root)
    manifest_path = root / "examples" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest:
        raise ValueError("The contract example manifest is empty")
    declared = set()
    declared_files = set()
    covered_schemas = set()
    covered_definitions = set()
    for entry in manifest:
        schema_name, schema_fragment = urldefrag(entry["schema"])
        example_name, example_fragment = urldefrag(entry["example"])
        example = _local_path(root, example_name)
        target = (example, example_fragment)
        if target in declared:
            raise ValueError(f"Duplicate example in manifest: {entry['example']}")
        declared.add(target)
        declared_files.add(example)
        select_validator(validators, entry["schema"]).validate(
            example_payload(root, entry["example"])
        )
        covered_schemas.add(schema_name)
        if schema_fragment:
            covered_definitions.add((schema_name, schema_fragment))
    examples = set((root / "examples").rglob("*.json")) - {manifest_path}
    if declared_files != {path.resolve() for path in examples}:
        raise ValueError("The manifest must declare every example file")
    if covered_schemas != set(validators):
        raise ValueError("Every schema must have at least one declared example")
    required_definitions = {
        (name, f"/$defs/{definition}")
        for name, validator in validators.items()
        for definition in validator.schema.get("$defs", {})
    }
    if not required_definitions.issubset(covered_definitions):
        raise ValueError("Every named definition must have a targeted example")
    return len(manifest)


def bundle_schemas(root: Path, *, tolerant: bool = False) -> dict:
    schemas = load_schemas(root)
    targets = {}
    definitions = {}
    root_names = []
    for schema in schemas.values():
        candidates = [("", schema["title"], schema)]
        candidates.extend(
            (f"/$defs/{name}", name, value) for name, value in schema.get("$defs", {}).items()
        )
        if schema["$id"] == "urn:close-out-demo:contracts:codes":
            candidates.extend(
                (f"/properties/{name}", name, value) for name, value in schema["properties"].items()
            )
        root_names.append(schema["title"])
        for fragment, name, value in candidates:
            if name in definitions:
                raise ValueError(f"Duplicate generated definition: {name}")
            targets[(schema["$id"], fragment)] = name
            definitions[name] = (schema["$id"], value)

    def rewrite(value, document_id):
        if isinstance(value, list):
            return [rewrite(item, document_id) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in {"$id", "$schema", "$defs"}:
                continue
            if key == "$ref":
                uri, fragment = urldefrag(item)
                target_id = uri or document_id
                if (target_id, fragment) in targets:
                    result[key] = "#/$defs/" + targets[(target_id, fragment)]
                else:
                    raise ValueError(f"Unregistered local schema reference: {item}")
            else:
                result[key] = rewrite(item, document_id)
        return result

    bundled = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ContractTypes",
        "type": "object",
        "properties": {name: {"$ref": f"#/$defs/{name}"} for name in root_names},
        "additionalProperties": False,
        "$defs": {
            name: {"title": name, **rewrite(copy.deepcopy(value), uri)}
            for name, (uri, value) in definitions.items()
        },
    }
    return reader_schema(bundled) if tolerant else bundled


def main() -> None:
    from shared.contract_checks import validate_supporting_files

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("contracts"))
    args = parser.parse_args()
    count = validate_examples(args.root.resolve())
    validate_supporting_files(args.root.resolve())
    print(f"Validated {count} contract examples")


if __name__ == "__main__":
    main()
