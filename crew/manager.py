"""Magentic manager that forces the progress ledger into structured output.

Why this exists: the manager model answers the plain-text JSON instruction
with a reasoning-only response and empty text, so the parent's ledger parse
fails on every attempt ("No JSON object found") and the round is lost to a
reset. Forcing a JSON schema on the ledger call makes the API return a
message matching the schema. Only the ledger call is formatted; plan,
replan, and the final answer stay plain text.

Plumbing, verified against installed agent-framework 1.26.0: the format
must go in ``options`` on ``Agent.run``, not ``client_kwargs`` — the
Responses client reads ``response_format`` from options only and turns it
into ``text.format`` for the API; a ``client_kwargs`` value would be
silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_framework import Message
from agent_framework.orchestrations import (
    MagenticContext,
    MagenticProgressLedger,
    StandardMagenticManager,
)

# Private helpers from the framework's magentic module. The repo pins
# agent-framework==1.26.0 exactly, so this import is guarded by the pin;
# re-check it on any upgrade. MagenticProgressLedger itself comes from
# the public orchestrations namespace; only the JSON extractor is private.
from agent_framework_orchestrations._magentic import _extract_json, _team_block

logger = logging.getLogger(__name__)


def _ledger_item(answer_type: str) -> dict[str, Any]:
    """One ledger field: a reason string plus a typed answer."""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "answer": {"type": answer_type},
        },
        "required": ["reason", "answer"],
        "additionalProperties": False,
    }


LEDGER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "progress_ledger",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_request_satisfied": _ledger_item("boolean"),
                "is_in_loop": _ledger_item("boolean"),
                "is_progress_being_made": _ledger_item("boolean"),
                "next_speaker": _ledger_item("string"),
                "instruction_or_question": _ledger_item("string"),
            },
            "required": [
                "is_request_satisfied",
                "is_in_loop",
                "is_progress_being_made",
                "next_speaker",
                "instruction_or_question",
            ],
            "additionalProperties": False,
        },
    },
}
"""Chat-style response_format for the ledger call, mirroring the schema in
ORCHESTRATOR_PROGRESS_LEDGER_PROMPT. Strict mode needs every property in
``required`` and ``additionalProperties: false`` at every level."""


class StructuredLedgerManager(StandardMagenticManager):
    """StandardMagenticManager with the progress ledger forced to JSON.

    Mirrors the parent's ``create_progress_ledger`` (agent-framework
    1.26.0) with two changes: the ledger call passes the response format,
    and a failed attempt feeds its parse error back into the next
    attempt's prompt instead of re-sending the identical input. The raw
    reply is logged on failure, so a future failure is visible instead
    of a bare "No JSON object found". Re-check this method against the
    parent on any framework upgrade.
    """

    async def _complete(
        self,
        messages: list[Message],
        options: dict[str, Any] | None = None,
    ) -> Message:
        """Parent's _complete, plus per-call chat options.

        ``options=None`` preserves the parent's behaviour for plan, replan,
        and the final answer; the ledger call passes the response format.
        """
        session = self._agent.create_session()
        response = await self._agent.run(messages, session=session, options=options)
        if not response.messages:
            raise RuntimeError("Agent returned no messages in response.")
        if len(response.messages) > 1:
            logger.warning("Agent returned multiple messages; using the last one.")
        return response.messages[-1]

    async def create_progress_ledger(
        self, magentic_context: MagenticContext
    ) -> MagenticProgressLedger:
        """Use the model to produce a JSON progress ledger based on the conversation so far.

        Adds lightweight retries with backoff for transient parse issues and avoids selecting a
        non-existent "unknown" agent. If there are no participants, a clear error is raised.
        """
        agent_names = list(magentic_context.participant_descriptions.keys())
        if not agent_names:
            raise RuntimeError("No participants configured; cannot determine next speaker.")

        names_csv = ", ".join(agent_names)
        team_text = _team_block(magentic_context.participant_descriptions)

        prompt = self.progress_ledger_prompt.format(
            task=magentic_context.task,
            team=team_text,
            names=names_csv,
        )

        # Include full context to help the model decide current stage, with small retry loop
        attempts = 0
        last_error: Exception | None = None
        unknown_speaker_ledger: MagenticProgressLedger | None = None
        while attempts < self.progress_ledger_retry_count:
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply could not be parsed as JSON: "
                    f"{last_error}. Reply with a single JSON object matching the "
                    "schema and nothing else."
                )
            user_message = Message(role="user", contents=[attempt_prompt])
            raw = await self._complete(
                [*magentic_context.chat_history, user_message],
                options={"response_format": LEDGER_RESPONSE_FORMAT},
            )
            try:
                ledger_dict = _extract_json(raw.text)
                ledger = MagenticProgressLedger.from_dict(ledger_dict)
            except Exception as ex:
                last_error = ex
                unknown_speaker_ledger = None
                logger.warning(f"Progress ledger raw reply: {raw.text[:500]!r}")
            else:
                next_speaker = ledger.next_speaker.answer
                if (
                    ledger.is_request_satisfied.answer
                    or not isinstance(next_speaker, str)
                    or next_speaker in agent_names
                ):
                    return ledger
                # Retry an unknown name, but keep the ledger so running out of attempts ends the run
                # through the orchestrator's invalid speaker handling instead of another reset.
                unknown_speaker_ledger = ledger
                last_error = ValueError(f"Unknown next speaker '{next_speaker}'. Valid names: {names_csv}.")
            attempts += 1
            logger.warning(
                f"Progress ledger attempt failed ({attempts}/{self.progress_ledger_retry_count}): {last_error}"
            )
            if attempts < self.progress_ledger_retry_count:
                # brief backoff before next try
                await asyncio.sleep(0.25 * attempts)

        if unknown_speaker_ledger is not None:
            return unknown_speaker_ledger
        raise RuntimeError(
            f"Progress ledger parse failed after {self.progress_ledger_retry_count} attempt(s): {last_error}"
        )


__all__ = ["LEDGER_RESPONSE_FORMAT", "StructuredLedgerManager"]
