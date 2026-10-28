"""Local stdio MCP. The tool is explicitly provisional, not a guessed Fabric API."""

import argparse
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from shared.data_agent_stub import DataAgentStub
from shared.dependency_modes import DependencyModes
from shared.seed import load_seed


def create_server(service: DataAgentStub) -> FastMCP:
    server = FastMCP("close-out-local-stub")

    @server.tool()
    def get_site_facts(site_id: str, scenario: str = "initial-blocked") -> dict:
        """Return synthetic facts only for sites allowed when this local server was started."""
        return service.site_facts(site_id, scenario)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=Path("seed") / "seed.json")
    parser.add_argument("--contracts", type=Path, default=Path("contracts"))
    parser.add_argument(
        "--validation-results",
        type=Path,
        help="Optional contract-validated local photo results from actual agent inference",
    )
    parser.add_argument(
        "--site", action="append", required=True, help="Local session site allowlist"
    )
    args = parser.parse_args()
    DependencyModes.from_env(os.environ).require_offline()
    results = (
        json.loads(args.validation_results.read_text(encoding="utf-8"))
        if args.validation_results
        else None
    )
    service = DataAgentStub(
        load_seed(args.seed, args.contracts), set(args.site), results, args.contracts
    )
    create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
