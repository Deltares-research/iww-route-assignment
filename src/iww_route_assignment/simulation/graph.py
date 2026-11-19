"""OpenTNSim graph for an assignment network."""

from __future__ import annotations

import networkx as nx
from shapely import Point

from ..assignment.network import AssignmentNetwork


def build_opentnsim_graph(network: AssignmentNetwork) -> nx.DiGraph:
    """Directed graph with both directions per edge, in the form OpenTNSim vessels expect.

    Nodes are the network node ids and hold a ``geometry`` point (lon, lat). Edges hold
    ``length_m``, ``speed_m_s`` and ``edge_index``, the position in ``network.edges``.
    """
    graph = nx.DiGraph()
    for node_id, (lon, lat) in zip(network.node_ids, network.node_lon_lat, strict=True):
        graph.add_node(node_id, geometry=Point(float(lon), float(lat)))
    for index, edge in enumerate(network.edges):
        attributes = {
            "length_m": edge.distance_km * 1000.0,
            "speed_m_s": edge.speed_km_per_h / 3.6,
            "edge_index": index,
        }
        graph.add_edge(edge.from_node, edge.to_node, **attributes)
        graph.add_edge(edge.to_node, edge.from_node, **attributes)
    return graph
