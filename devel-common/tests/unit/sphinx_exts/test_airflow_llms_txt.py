# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import pickle
import subprocess
import sys
from pathlib import Path

import pytest

SPHINX_EXTS_PATH = Path(__file__).parents[3] / "src" / "sphinx_exts"


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
def test_example_links_preserve_doctree_cache_reuse(tmp_path, parallel):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    cache = tmp_path / "doctrees"
    reads = tmp_path / "reads.txt"
    example = source / "example.py"
    example.write_text("import math\n\nanswer = math.sqrt(4)\nprint(answer)\n")
    (source / "index.rst").write_text(
        "Examples\n========\n\n"
        "This introduction is long enough to be a page description.\n\n"
        ".. exampleinclude:: example.py\n   :language: python\n   :lines: 3-4\n"
    )
    (source / "conf.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(SPHINX_EXTS_PATH)!r})\n"
        "extensions = ['sphinx.ext.viewcode', 'exampleinclude', 'airflow_llms_txt']\n"
        "project = 'Cache reuse'\n"
        "version = release = '1.0'\n"
        "html_theme = 'basic'\n"
        f"llms_txt_build_parallel = {parallel!r}\n"
        "llms_txt_suffix_mode = 'append'\n"
        "llms_txt_site_url = 'https://example.org/docs/1.0/'\n"
        "llms_txt_source_url = 'https://github.com/apache/airflow/blob/test-tag'\n"
        f"llms_txt_repo_root = {str(source)!r}\n"
        f"exampleinclude_sourceroot = {str(source)!r}\n"
        "def record_read(app, tree):\n"
        f"    with open({str(reads)!r}, 'a') as stream:\n"
        "        stream.write(app.builder.name + '\\n')\n"
        "def setup(app):\n"
        "    app.connect('doctree-read', record_read)\n"
    )

    for phase, html_read_count in [
        ("cold", 1),
        ("legacy-cache", 1),
        ("unchanged", 0),
        ("dependency-changed", 1),
    ]:
        reads.unlink(missing_ok=True)
        if phase == "legacy-cache":
            with (cache / "environment.pickle").open("rb") as stream:
                environment = pickle.load(stream)
            environment.version.pop("airflow_llms_txt", None)
            with (cache / "environment.pickle").open("wb") as stream:
                pickle.dump(environment, stream)
        if phase == "dependency-changed":
            example.write_text("import math\n\nanswer = math.sqrt(9)\nprint(answer)\n")
        subprocess.run(
            [sys.executable, "-m", "sphinx", "-b", "html", "-d", str(cache), str(source), str(output)],
            check=True,
        )

        read_builders = reads.read_text().splitlines() if reads.exists() else []
        assert read_builders.count("html") == html_read_count
        assert read_builders.count("llms-markdown") == int(parallel)

        html = (output / "index.html").read_text()
        markdown = (output / "index.html.md").read_text()
        assert 'href="_modules/example.html"' in html
        assert "Excerpt from" not in html
        assert "https://github.com/apache/airflow/blob/test-tag/example.py" in markdown
        assert "_modules/example.html" not in markdown
        assert ("sqrt(9)" if phase == "dependency-changed" else "sqrt(4)") in markdown

        with (cache / "index.doctree").open("rb") as stream:
            tree = pickle.load(stream)
        assert "Excerpt from" not in tree.astext()
        assert any(
            node.get("example_source_filename") == str(example)
            for node in tree.findall()
            if hasattr(node, "get")
        )
