# Knowledge graph (Neo4j) (`graph`)

The Graph page: how your places, trips, roads and stations connect.

How to use it: [docs/graph.md](../../../docs/graph.md).

| | |
|---|---|
| Uses when there | [events](../events/), [gridserve](../gridserve/) |
| Endpoints | `/v1/graph`, `/graph` |
| Scheduled jobs | `graph_build` |
| Assistant tools | `connections` |
| Plugs into | `assistant.tools`, `shutdown`, `setup.check` |

## Files

- `__init__.py`: The knowledge graph: relationships derived from Postgres, held in Neo4j.
- `build.py`: Rebuild the knowledge graph from Postgres.
- `checks.py`: The graph's line on the Setup page ("Your setup"), plugged in through the registry ("setup.check").
- `explore.py`: Exploring the knowledge graph from the Graph page: search, expand a node, presets, and your own Cypher.
- `graph_page.py`: The Graph page: the knowledge graph as nodes and edges you can search, expand and query.
- `graph_routes.py`: Walking the knowledge graph: what is connected to what, and what is disrupting a place.
- `model.py`: What the knowledge graph is made of, and how it is derived from what Postgres already holds.
- `store.py`: Talking to Neo4j: writing the derived graph, and the few traversals the assistant needs.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._
