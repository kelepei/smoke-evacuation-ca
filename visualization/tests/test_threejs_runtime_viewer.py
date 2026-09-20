from __future__ import annotations

import unittest
from pathlib import Path


VIEWER_PATH = Path(__file__).resolve().parents[1] / "prototype" / "threejs_runtime_viewer.html"


class ThreeJsRuntimeViewerTests(unittest.TestCase):
    def test_import_map_resolves_three_and_addons_from_one_version(self) -> None:
        page = VIEWER_PATH.read_text(encoding="utf-8")
        version = "three@0.160.0"
        self.assertIn('<script type="importmap">', page)
        self.assertIn(f'"three":"https://cdn.jsdelivr.net/npm/{version}/build/three.module.js"', page)
        self.assertIn(f'"three/addons/":"https://cdn.jsdelivr.net/npm/{version}/examples/jsm/"', page)
        self.assertIn("import * as THREE from 'three';", page)
        self.assertIn("import {OrbitControls} from 'three/addons/controls/OrbitControls.js';", page)

    def test_reload_replaces_runtime_root_instead_of_accumulating_meshes(self) -> None:
        page = VIEWER_PATH.read_text(encoding="utf-8")
        self.assertIn("let root=new THREE.Group();scene.add(root);const people=new Map(), previous=new Map();", page)
        self.assertIn("function clear(){root.removeFromParent();root=new THREE.Group();scene.add(root);people.clear()}", page)
        self.assertIn("function load(snapshot){if(!valid(snapshot))throw Error", page)
        self.assertIn("clear();const g=snapshot.grid", page)
