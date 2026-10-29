"""远程关系对信息传播测试。"""

from __future__ import annotations

import unittest

import networkx as nx

from social.information_diffusion import InformationDiffusionEngine
from social.information_state import InfoState, InformationStateEngine


class _Person:
    def __init__(self, pid: int):
        self.id = pid
        self.evacuated = False
        self.is_dead = False


class _Graph:
    def __init__(self, persons, graph):
        self.persons = persons
        self.graph = graph

    def get_relation(self, left, right):
        return {"relation_type": "family", "strength": 0.95, "trust": 0.95}


class RemotePairSpreadTests(unittest.TestCase):
    def _engine(self):
        persons = {1: _Person(1), 2: _Person(2)}
        graph = nx.DiGraph()
        graph.add_edge(1, 2)
        wrapper = _Graph(persons, graph)
        info = InformationStateEngine(wrapper)
        info.initialize_person(1)
        info.initialize_person(2)
        diffusion = InformationDiffusionEngine(wrapper, info, 10, 10)
        return info, diffusion, [persons[1], persons[2]]

    def test_informed_person_tells_relation_partner(self) -> None:
        info, diffusion, people = self._engine()
        info.transition_state(1, InfoState.ALERTED, 0, source=1, method="test")
        info.transition_state(1, InfoState.CONFIRMED, 1, source=1, method="test")
        diffusion.remote_pair_params.update({"pairs_per_step": 1, "probability": 1.0})
        diffusion._apply_remote_pair_spread(people, 1)
        # UNKNOWN 不能直接到 CONFIRMED，远程告知先到 ALERTED
        self.assertEqual(info.get_state_value(2), "ALERTED")
        self.assertEqual(diffusion.step_stats["remote_pair_spread"], 1)

    def test_zero_probability_does_not_spread(self) -> None:
        info, diffusion, people = self._engine()
        info.transition_state(1, InfoState.ALERTED, 0, source=1, method="test")
        info.transition_state(1, InfoState.CONFIRMED, 1, source=1, method="test")
        diffusion.remote_pair_params.update({"pairs_per_step": 1})
        diffusion.remote_pair_params["relation_probabilities"]["family"] = 0.0
        diffusion._apply_remote_pair_spread(people, 1)
        self.assertEqual(info.get_state_value(2), "UNKNOWN")

    def test_relation_specific_probabilities(self) -> None:
        _info, diffusion, _people = self._engine()
        mapping = diffusion.remote_pair_params["relation_probabilities"]
        self.assertEqual(mapping["family"], 1.0)
        self.assertEqual(mapping["friend"], 0.7)
        self.assertEqual(mapping["classmate"], 0.5)


if __name__ == "__main__":
    unittest.main()
