"""
Tests for AnswerGenerator.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.ai.answer_generator import AnswerGenerator
from app.models.result import SearchResult


class TestAnswerGenerator:
    def test_empty_sources_returns_friendly_message(self):
        client = MagicMock()
        generator = AnswerGenerator(client)

        import asyncio
        response = asyncio.run(generator.generate("What is quantum computing?", []))
        assert "No reliable search results were found" in response

    @pytest.mark.asyncio
    async def test_generate_calls_llm_with_sources(self):
        client = MagicMock()
        client.chat = AsyncMock(return_value="Quantum computing uses superposition. [1]")
        generator = AnswerGenerator(client)

        results = [
            SearchResult(
                url="https://ibm.com",
                title="IBM Quantum",
                snippet="Superposition and entanglement",
                content="Quantum computing leverages superposition...",
            )
        ]

        answer = await generator.generate("What is quantum computing?", results)
        assert "Quantum computing" in answer
        assert client.chat.called
        call_kwargs = client.chat.call_args.kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert "IBM Quantum" in messages[1]["content"]
