"""Cycle ordering must not change forecasts when Python's hash seed changes."""
import json
import os
import subprocess
import sys


def test_cyclic_upstream_lengths_do_not_depend_on_process_hash_seed():
    source = """
import json
import networkx as nx
from dipcast.network.rivers import RiverNetwork
net = RiverNetwork.__new__(RiverNetwork)
net.graph = nx.DiGraph()
for a, b, length in [('head', 'alpha', 1000), ('alpha', 'beta', 200),
                     ('beta', 'gamma', 300), ('gamma', 'alpha', 400), ('gamma', 'out', 500)]:
    net.graph.add_edge(a, b, length=length)
print(json.dumps(net._compute_upstream_m(quiet=True), sort_keys=True))
"""
    results = [json.loads(subprocess.check_output([sys.executable, '-c', source],
               env={**os.environ, 'PYTHONHASHSEED': str(seed)}, text=True)) for seed in (1, 2, 13)]
    assert results[0] == results[1] == results[2]
    assert results[0]['out'] >= 1000
