"""Measure what progressive disclosure actually saves.

Registering every action as a first-class tool costs context proportional to
the catalog; the three meta-tools cost a constant. This script prints both,
projected across catalog sizes, so the architecture claim is a number rather
than an assertion.

    python scripts/benchmark_context.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_composio import OpenComposio  # noqa: E402
from open_composio.mcp_server import build_mcp_server  # noqa: E402

# ~4 characters per token is the usual English/JSON approximation. Good enough
# for an order-of-magnitude comparison; swap in a real tokenizer if you need
# exact figures.
CHARS_PER_TOKEN = 4


def tokens(payload) -> int:
    return len(json.dumps(payload)) // CHARS_PER_TOKEN


def main() -> None:
    oc = OpenComposio(audit=False, load_upstreams=False)
    tools = oc.get_tools(connected_only=False)
    per_tool = [tokens(t.as_anthropic()) for t in tools]
    avg = sum(per_tool) / len(per_tool)

    server = build_mcp_server(oc)
    meta = [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in asyncio.run(server.list_tools())
    ]
    meta_cost = tokens(meta)

    print(f"Catalog sampled: {len(tools)} actions, avg {avg:.0f} tokens/tool schema")
    print(f"Meta-tool surface ({len(meta)} tools): {meta_cost} tokens — constant\n")

    print(f"{'catalog size':>13} | {'all tools':>12} | {'meta-tools':>11} | {'saving':>8}")
    print("-" * 54)
    for size in (10, 50, 100, 250, 600, 1000):
        direct = int(avg * size)
        saving = 1 - (meta_cost / direct)
        print(f"{size:>13} | {direct:>12,} | {meta_cost:>11,} | {saving:>7.1%}")

    print(
        "\nA 200k-token context holds roughly "
        f"{int(200_000 / avg):,} tool schemas registered directly; "
        "with meta-tools the catalog size is irrelevant."
    )


if __name__ == "__main__":
    main()
