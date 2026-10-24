"""
OpenAI client wrapper for SeekAI.

Provides a thin, reusable client around the OpenAI Responses API.
All model configuration is driven by environment variables.
API keys are never logged.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Optional, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AIClient:
    """Async wrapper around OpenAI's chat completions API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.openai_api_key
        self._model = model or settings.openai_model
        self._base_url = base_url or settings.openai_base_url
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        """Lazily initialize the AsyncOpenAI client."""
        if self._client is None:
            # Use provided key or a placeholder for offline testing/mocking
            key = self._api_key or "placeholder-key"
            kwargs = {"api_key": key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Send messages to the chat completions endpoint and return the text.

        Args:
            messages:        List of {"role": ..., "content": ...} dicts.
            temperature:     Sampling temperature (lower = more deterministic).
            max_tokens:      Maximum response tokens.
            response_format: Optional dict for structured output (e.g., JSON mode).

        Returns:
            String content of the first message choice.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        logger.debug(
            "OpenAI request | model=%s | messages=%d | max_tokens=%d",
            self._model, len(messages), max_tokens,
        )

        if not self.is_available():
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        response = await self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        logger.debug("OpenAI response length: %d chars", len(content))
        return content

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """
        Stream tokens from OpenAI chat completions.

        Yields:
            Token strings as they arrive from the model.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        logger.debug(
            "OpenAI streaming request | model=%s | messages=%d",
            self._model, len(messages),
        )

        if not self.is_available():
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        response = await self.client.chat.completions.create(**kwargs)
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        response_model: Type[T],
        temperature: float = 0.1,
    ) -> T:
        """
        Send a chat request and parse the JSON response into a Pydantic model.

        Uses JSON mode to ensure valid JSON output, then validates with Pydantic.

        Args:
            messages:       Chat messages to send.
            response_model: Pydantic BaseModel subclass to parse the response into.
            temperature:    Sampling temperature.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If the response cannot be parsed into the model.
        """
        import json

        text = await self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", text[:200])
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

        try:
            return response_model.model_validate(data)
        except Exception as exc:
            logger.error("Failed to validate LLM response as %s: %s", response_model.__name__, exc)
            raise ValueError(f"LLM response validation failed: {exc}") from exc
