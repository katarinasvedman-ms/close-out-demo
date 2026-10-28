import { readFile, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

const [input, output] = process.argv.slice(2);
const schema = JSON.parse(await readFile(input, "utf8"));
const types = await compile(schema, "ContractTypes", {
  bannerComment: "/* Generated from contracts; do not edit. Reader codes are forward-compatible strings. */",
  unreachableDefinitions: true,
  style: { singleQuote: false, semi: true },
});
await writeFile(output, types, "utf8");
