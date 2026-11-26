"""Graph-referenced and graph-ingested study sources are admitted by digest."""

from __future__ import annotations

import hashlib

import pytest

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.study_intake import (
    FilesystemSource,
    GraphRefSource,
    admit_study_sources,
)

BODY = b"graph body"
DIGEST = hashlib.sha256(BODY).hexdigest()


def graph_source(**changes):
    return {
        "kind": "graph_ref",
        "tenant_id": "t",
        "graph": "g",
        "node": "n",
        "revision": 3,
        "content_digest": DIGEST,
        **changes,
    }


async def reader(tenant, graph, node, revision):
    assert (tenant, graph, node, revision) == ("t", "g", "n", 3)
    return BODY


@pytest.mark.spec("MERISTEM-RUN-R003")
@pytest.mark.spec("MERISTEM-RUN-R003.1")
async def test_graph_ref_source_admitted():
    admitted = await admit_study_sources(
        [graph_source(), {"kind": "filesystem", "path": "p", "digest": "d"}], reader
    )
    assert admitted == (
        GraphRefSource("t", "g", "n", 3, DIGEST),
        FilesystemSource("p", "d"),
    )


@pytest.mark.spec("MERISTEM-RUN-R003.1")
async def test_graph_ingested_source_accepted():
    admitted = await admit_study_sources([graph_source(kind="graph_ingested")], reader)
    assert admitted[0].content_digest == DIGEST


@pytest.mark.spec("MERISTEM-RUN-R003.1")
@pytest.mark.parametrize(
    ("source", "code"),
    [
        (graph_source(node=""), "STUDY_SOURCE_FIELD_MISSING"),
        (graph_source(content_digest="ABC"), "STUDY_SOURCE_FIELD_MISSING"),
        (graph_source(content_digest="0" * 64), "STUDY_SOURCE_DIGEST_MISMATCH"),
    ],
)
async def test_refusal_codes(source, code):
    with pytest.raises(ConsumerRefused, match=code):
        await admit_study_sources([source], reader)


@pytest.mark.spec("MERISTEM-RUN-R003.1")
async def test_run_refuses_a_mismatched_graph_source_before_any_step():
    import ports_support as support
    from pydantic_ai.models.test import TestModel

    from meristem.graph import RunPlan, run

    async def tampered(tenant, graph, node, revision):
        return b"other body"

    support.PORTS.study_content = tampered
    plan = RunPlan(
        model=TestModel(custom_output_text="ok"),
        steps=("q",),
        study_sources=(graph_source(),),
    )
    result = await run(support.request(task="t"), plan=plan)
    assert result.status.value == "failed"
    assert "STUDY_SOURCE_DIGEST_MISMATCH" in (result.error or "")
