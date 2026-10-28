import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import manifest from "../../../contracts/examples/manifest.json";
import type {
  CreateReworkRequest,
  PhotoUploaded,
  ValidationResult,
} from "../../shared/contracts/models";
import envelope from "../../../contracts/events/event-envelope.schema.json";
import readiness from "../../../contracts/activator/readiness-signal.schema.json";
import uploaded from "../../../contracts/examples/photo-uploaded.json";
import beforeCase from "../../../contracts/examples/pre-case-upload.json";
import blocked from "../../../contracts/examples/readiness-blocked.json";
import ready from "../../../contracts/examples/readiness-ready.json";
import objects from "../../../contracts/examples/shared-objects.json";
import actions from "../../../contracts/examples/actions.json";

const ajv = new Ajv2020({ strict: true });
addFormats(ajv);
const schemas = import.meta.glob<Record<string, unknown>>("../../../contracts/**/*.schema.json", {
  eager: true,
  import: "default",
});
const exampleFiles = import.meta.glob("../../../contracts/examples/*.json", {
  eager: true,
  import: "default",
});
for (const schema of Object.values(schemas)) ajv.addSchema(schema);
const validateEnvelope = ajv.getSchema(envelope.$id)!;
const validateReadiness = ajv.getSchema(readiness.$id)!;

type Assert<T extends true> = T;
type RequiredKeys<T> = {
  [K in keyof T]-?: object extends Pick<T, K> ? never : K;
}[keyof T];
export type GeneratedContractShapeChecks = [
  Assert<"reason" extends RequiredKeys<CreateReworkRequest> ? true : false>,
  Assert<"photoId" extends RequiredKeys<PhotoUploaded["data"]> ? true : false>,
  Assert<"confidence" extends keyof ValidationResult ? true : false>,
];

function pointer(document: unknown, fragment: string): unknown {
  return fragment
    ? fragment.slice(1).split("/").reduce<unknown>((value, key) =>
        (value as Record<string, unknown>)[key.replaceAll("~1", "/").replaceAll("~0", "~")],
      document)
    : document;
}

describe("cross-language contract examples", () => {
  it.each(manifest)("validates $schema from $example", (entry) => {
    const [schemaPath, schemaPointer = ""] = entry.schema.split("#");
    const schema = schemas[`../../../contracts/${schemaPath}`];
    const [file, fragment = ""] = entry.example.split("#");
    const payload = pointer(exampleFiles[`../../../contracts/${file}`], fragment);
    const validate = ajv.getSchema(`${schema.$id}${schemaPointer ? "#" + schemaPointer : ""}`)!;
    expect(validate(payload), JSON.stringify(validate.errors)).toBe(true);
  });

  it.each([uploaded, beforeCase])("accepts envelope $eventId", (payload) => {
    expect(validateEnvelope(payload), JSON.stringify(validateEnvelope.errors)).toBe(true);
  });

  it.each([blocked, ready])("accepts readiness $readiness", (payload) => {
    expect(validateReadiness(payload), JSON.stringify(validateReadiness.errors)).toBe(true);
  });

  it("requires caseId even when an upload precedes case creation", () => {
    const payload: Record<string, unknown> = { ...beforeCase };
    delete payload.caseId;
    expect(validateEnvelope(payload)).toBe(false);
  });

  it("rejects a timestamp without a timezone", () => {
    expect(validateEnvelope({ ...uploaded, occurredAt: "2026-10-27T09:00:00" })).toBe(false);
  });

  it("allows unknown envelope fields", () => {
    expect(validateEnvelope({ ...uploaded, futureField: true })).toBe(true);
  });

  it("rejects Ready while unmet conditions remain", () => {
    expect(validateReadiness({ ...ready, readinessMissing: ["PL-0042-01"] })).toBe(false);
  });

  it.each([true, false])("rejects validation with both targets: %s", (both) => {
    const payload: Record<string, unknown> = { ...objects.ValidationResult };
    if (both) payload.artifactId = "ART-0042-RFDS";
    else delete payload.photoId;
    const validate = ajv.getSchema("urn:close-out-demo:contracts:models#/$defs/ValidationResult")!;
    expect(validate(payload)).toBe(false);
  });

  it("requires photo capture metadata without requiring it for documents", () => {
    const payload: Record<string, unknown> = { ...actions.UploadMetadata };
    delete payload.capturedAt;
    const validate = ajv.getSchema("urn:close-out-demo:contracts:actions#/$defs/UploadMetadata")!;
    expect(validate(payload)).toBe(false);
    payload.artifactType = "rfds";
    expect(validate(payload), JSON.stringify(validate.errors)).toBe(true);
  });
});
