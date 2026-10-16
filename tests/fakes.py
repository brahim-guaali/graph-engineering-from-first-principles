"""A scripted stand-in for Gemini, so the tests run without a key or network."""
from types import SimpleNamespace

from google.genai import types

from agentkit.llm import Usage


class ScriptedChat:
    """Replays a list of turns. A turn is a list of (tool, args), or a string (final answer)."""

    def __init__(self, turns):
        self.turns, self.usage = list(turns), Usage()

    def chat(self, system, contents, tools):
        turn = self.turns.pop(0)
        self.usage.calls += 1
        self.usage.input_tokens += sum(len(str(c)) for c in contents) // 4
        if isinstance(turn, str):
            parts = [types.Part.from_text(text=turn)]
            calls = None
        else:
            parts = [types.Part.from_function_call(name=n, args=a) for n, a in turn]
            calls = [SimpleNamespace(name=n, args=a) for n, a in turn]
        content = types.Content(role="model", parts=parts)
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)],
                               function_calls=calls, text=turn if isinstance(turn, str) else None)


class ScriptedStructured:
    """Answers structured() calls with a function of (schema name, prompt)."""

    def __init__(self, answer):
        self.answer, self.usage = answer, Usage()

    def structured(self, system, prompt, schema):
        self.usage.calls += 1
        self.usage.input_tokens += len(prompt) // 4
        return schema.model_validate(self.answer(schema.__name__, prompt))
