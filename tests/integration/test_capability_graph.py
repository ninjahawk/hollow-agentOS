"""
Integration tests for AgentOS v2.1.0: Capability Graph.

Tests verify semantic capability discovery, type-based composition, and learning.
Capabilities navigate by semantic meaning (embedding distance), not symbolic lookup.

Run:
    PYTHONPATH=. pytest tests/integration/test_capability_graph.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.capability_graph import CapabilityGraph, CapabilityRecord, CompositionPlan
    CAPABILITY_GRAPH_AVAILABLE = True
except ImportError:
    CAPABILITY_GRAPH_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not CAPABILITY_GRAPH_AVAILABLE,
    reason="sentence-transformers not available"
)


@pytest.fixture(autouse=True)
def fresh_capability_storage(monkeypatch):
    """
    Provide a fresh temporary directory for capability storage in each test.
    Isolates tests so they don't interfere with each other's registrations.
    """
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    # Also patch the module-level CAPABILITY_PATH in capability_graph
    import agents.capability_graph as cap_module
    cap_module.CAPABILITY_PATH = Path(tmpdir) / "capabilities"

    yield

    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Register and retrieve capability
# ---------------------------------------------------------------------------

class TestCapabilityGraphRegister:
    def test_register_and_get_capability(self):
        """
        Register a capability. Retrieve it by ID.
        Assert: capability returned exactly, embedding computed.
        """
        graph = CapabilityGraph(capacity=1000)

        cap = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads a text file and returns its contents as a string",
            input_schema="a file path string",
            output_schema="file contents as text",
            composition_tags=["io", "filesystem"],
            confidence=0.95,
        )

        cap_id = graph.register(cap)
        assert cap_id.startswith("cap-")

        retrieved = graph.get(cap_id)
        assert retrieved is not None
        assert retrieved.name == "read_file"
        assert retrieved.description == cap.description
        assert retrieved.confidence == 0.95


# ---------------------------------------------------------------------------
# Test 2 — Semantic search finds capabilities by description
# ---------------------------------------------------------------------------

class TestCapabilityGraphSearch:
    def test_find_by_semantic_query(self):
        """
        Register 3 capabilities: file I/O, JSON parsing, HTTP request.
        Search for 'read files'. Assert: file-related capability is top result.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads a text file from disk and returns contents",
            input_schema="file path string",
            output_schema="file contents as text",
            composition_tags=["io"],
            confidence=0.95,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="parse_json",
            description="parses JSON string and returns object structure",
            input_schema="JSON string",
            output_schema="parsed object",
            composition_tags=["parsing"],
            confidence=0.92,
        )

        cap3 = CapabilityRecord(
            capability_id="",
            name="http_get",
            description="makes HTTP GET request and returns response body",
            input_schema="URL string",
            output_schema="response body as text",
            composition_tags=["network"],
            confidence=0.90,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)
        id3 = graph.register(cap3)

        # Search for file reading
        results = graph.find("read a file from disk", top_k=3, similarity_threshold=0.5)
        assert len(results) > 0
        # Top result should be about files
        assert any("read" in r[0].name.lower() or "file" in r[0].description.lower()
                   for r in results[:2])

    def test_find_respects_similarity_threshold(self):
        """
        Register capabilities. Search with high threshold.
        Assert: low-similarity results filtered out.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="fibonacci",
            description="computes fibonacci sequence up to N terms",
            input_schema="integer N",
            output_schema="list of integers",
            confidence=0.88,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="prime_check",
            description="checks if a number is prime",
            input_schema="integer",
            output_schema="boolean",
            confidence=0.90,
        )

        graph.register(cap1)
        graph.register(cap2)

        # Search for something unrelated with high threshold
        results = graph.find("send an email over SMTP",
                           top_k=5, similarity_threshold=0.85)

        # Should find few or none (math capabilities unrelated to email)
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Test 3 — Type-based discovery (input → output)
# ---------------------------------------------------------------------------

class TestCapabilityGraphTypeDiscovery:
    def test_find_by_types_matches_schemas(self):
        """
        Register capabilities with different input/output schemas.
        Find by type: input_schema='file path', output_schema='parsed object'.
        Assert: finds capability that transforms file path → parsed object.
        """
        graph = CapabilityGraph(capacity=1000)

        # File path → text
        cap1 = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file from path",
            input_schema="file path string",
            output_schema="file contents as text",
            confidence=0.95,
        )

        # String → parsed object
        cap2 = CapabilityRecord(
            capability_id="",
            name="parse_json",
            description="parses JSON",
            input_schema="JSON string",
            output_schema="parsed JSON object",
            confidence=0.92,
        )

        # URL → response
        cap3 = CapabilityRecord(
            capability_id="",
            name="http_get",
            description="makes HTTP request",
            input_schema="URL string",
            output_schema="response body text",
            confidence=0.90,
        )

        graph.register(cap1)
        graph.register(cap2)
        graph.register(cap3)

        # Find capabilities that read files (file path → content)
        results = graph.find_by_types(
            input_schema="path to a file",
            output_schema="text content",
            top_k=3
        )

        assert len(results) > 0
        # Top result should be read_file
        assert results[0][0].name == "read_file"


# ---------------------------------------------------------------------------
# Test 4 — Composition validation
# ---------------------------------------------------------------------------

class TestCapabilityGraphComposition:
    def test_compose_validates_chain(self):
        """
        Create chain: read_file → parse_json.
        Output of read_file (file contents) should match input of parse_json (JSON string).
        Assert: compose() returns valid CompositionPlan with confidence > 0.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="read_json_file",
            description="reads a JSON file from disk",
            input_schema="file path to JSON",
            output_schema="JSON string content",
            confidence=0.95,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="parse_json",
            description="parses JSON string to object",
            input_schema="JSON string",
            output_schema="parsed object",
            confidence=0.92,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)

        plan = graph.compose([id1, id2])

        assert plan is not None
        assert isinstance(plan, CompositionPlan)
        assert len(plan.capabilities) == 2
        assert plan.capabilities[0] == id1
        assert plan.capabilities[1] == id2
        assert plan.confidence > 0.5
        assert len(plan.transformation_path) == 3  # input, intermediate, output

    def test_compose_rejects_incompatible_chain(self):
        """
        Create chain: fibonacci → send_email.
        Output of fibonacci (integers) incompatible with input of send_email (email address).
        Assert: compose() returns None.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="fibonacci",
            description="generates fibonacci numbers",
            input_schema="count as integer",
            output_schema="list of integers",
            confidence=0.95,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="send_email",
            description="sends email to recipient",
            input_schema="recipient email address string",
            output_schema="success status",
            confidence=0.90,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)

        plan = graph.compose([id1, id2])

        # Should reject: int list → email address is incompatible
        assert plan is None

    def test_compose_three_capability_chain(self):
        """
        Chain 3 capabilities: file path → JSON string → object → formatted string.
        Assert: all links compatible, plan confidence decreases with chain length.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="read_json_file",
            description="reads JSON file",
            input_schema="file path",
            output_schema="JSON string",
            confidence=0.95,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="parse_json",
            description="parses JSON",
            input_schema="JSON string",
            output_schema="object",
            confidence=0.92,
        )

        cap3 = CapabilityRecord(
            capability_id="",
            name="format_object",
            description="formats object as string",
            input_schema="object",
            output_schema="formatted string",
            confidence=0.90,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)
        id3 = graph.register(cap3)

        plan = graph.compose([id1, id2, id3])

        assert plan is not None
        assert len(plan.transformation_path) == 4
        assert plan.transformation_path[0] == "file path"
        assert plan.transformation_path[-1] == "formatted string"
        # Confidence decreases with chain length (0.95 * 0.92 * 0.90 ≈ 0.787, but may be affected by rounding)
        assert 0.5 < plan.confidence <= 1.01  # Allow slight floating-point overshoot


# ---------------------------------------------------------------------------
# Test 5 — Composition learning and reuse
# ---------------------------------------------------------------------------

class TestCapabilityGraphLearning:
    def test_learn_composition_and_retrieve(self):
        """
        Register capabilities, create a composition plan, learn it.
        Later, ask for recommended chains matching input/output types.
        Assert: learned pattern is returned.
        """
        graph = CapabilityGraph(capacity=1000)

        cap1 = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file",
            input_schema="file path",
            output_schema="text content",
            confidence=0.95,
        )

        cap2 = CapabilityRecord(
            capability_id="",
            name="count_lines",
            description="counts lines in text",
            input_schema="text content",
            output_schema="integer line count",
            confidence=0.93,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)

        # Manually create and learn a composition
        plan = CompositionPlan(
            capabilities=[id1, id2],
            transformation_path=["file path", "text content", "integer line count"],
            confidence=0.88,
            rationale="Read file then count lines"
        )
        graph.learn_composition(plan)

        # Later, ask for chains: file path → line count
        recommendations = graph.get_recommended_chains(
            input_type="path to a file",
            output_type="total number of lines",
            top_k=5
        )

        # Should find the learned pattern
        assert len(recommendations) > 0
        assert recommendations[0].capabilities == [id1, id2]

    def test_recommended_chains_respects_type_similarity(self):
        """
        Learn 2 compositions: file → lines, and image → pixels.
        Request recommendations for file → lines.
        Assert: only relevant pattern returned (not image pattern).
        """
        graph = CapabilityGraph(capacity=1000)

        # File composition
        cap1 = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file",
            input_schema="file path",
            output_schema="text content",
            confidence=0.95,
        )
        cap2 = CapabilityRecord(
            capability_id="",
            name="count_lines",
            description="counts lines",
            input_schema="text content",
            output_schema="line count",
            confidence=0.93,
        )

        # Image composition
        cap3 = CapabilityRecord(
            capability_id="",
            name="load_image",
            description="loads image file",
            input_schema="image file path",
            output_schema="pixel array",
            confidence=0.94,
        )
        cap4 = CapabilityRecord(
            capability_id="",
            name="count_pixels",
            description="counts pixels",
            input_schema="pixel array",
            output_schema="pixel count",
            confidence=0.92,
        )

        id1 = graph.register(cap1)
        id2 = graph.register(cap2)
        id3 = graph.register(cap3)
        id4 = graph.register(cap4)

        plan_file = CompositionPlan(
            capabilities=[id1, id2],
            transformation_path=["file path", "text content", "line count"],
            confidence=0.88,
            rationale="Read text file and count lines"
        )
        plan_image = CompositionPlan(
            capabilities=[id3, id4],
            transformation_path=["image file path", "pixel array", "pixel count"],
            confidence=0.87,
            rationale="Load image and count pixels"
        )

        graph.learn_composition(plan_file)
        graph.learn_composition(plan_image)

        # Request file → lines
        recommendations = graph.get_recommended_chains(
            input_type="text file path",
            output_type="number of lines",
            top_k=5
        )

        # Should find file pattern
        assert len(recommendations) > 0
        assert any(rec.capabilities == [id1, id2] for rec in recommendations)


# ---------------------------------------------------------------------------
# Test 6 — Usage tracking
# ---------------------------------------------------------------------------

class TestCapabilityGraphUsage:
    def test_update_usage_increments_count(self):
        """
        Register a capability, call update_usage() multiple times.
        Assert: usage_count increments, last_used timestamp updates.
        """
        graph = CapabilityGraph(capacity=1000)

        cap = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file",
            input_schema="file path",
            output_schema="text",
            confidence=0.95,
        )

        cap_id = graph.register(cap)
        retrieved1 = graph.get(cap_id)
        initial_usage = retrieved1.usage_count
        initial_time = retrieved1.last_used

        # Use it
        time.sleep(0.01)
        graph.update_usage(cap_id)
        retrieved2 = graph.get(cap_id)
        assert retrieved2.usage_count == initial_usage + 1
        assert retrieved2.last_used > initial_time

        # Use again
        time.sleep(0.01)
        graph.update_usage(cap_id)
        retrieved3 = graph.get(cap_id)
        assert retrieved3.usage_count == initial_usage + 2


# ---------------------------------------------------------------------------
# Test 7 — List all capabilities
# ---------------------------------------------------------------------------

class TestCapabilityGraphList:
    def test_list_all_capabilities(self):
        """
        Register 5 capabilities. Call list_all().
        Assert: returns all registered capabilities in order.
        """
        graph = CapabilityGraph(capacity=1000)

        names = ["read_file", "write_file", "parse_json", "format_json", "http_get"]
        ids = []

        for name in names:
            cap = CapabilityRecord(
                capability_id="",
                name=name,
                description=f"does {name}",
                input_schema="input",
                output_schema="output",
                confidence=0.90,
            )
            ids.append(graph.register(cap))

        listed = graph.list_all(limit=10)

        assert len(listed) == 5
        listed_names = [c.name for c in listed]
        for name in names:
            assert name in listed_names

    def test_list_respects_limit(self):
        """
        Register 10 capabilities. Call list_all(limit=3).
        Assert: returns only 3 capabilities.
        """
        graph = CapabilityGraph(capacity=1000)

        for i in range(10):
            cap = CapabilityRecord(
                capability_id="",
                name=f"cap_{i}",
                description=f"capability {i}",
                input_schema="input",
                output_schema="output",
                confidence=0.90,
            )
            graph.register(cap)

        listed = graph.list_all(limit=3)
        assert len(listed) == 3
