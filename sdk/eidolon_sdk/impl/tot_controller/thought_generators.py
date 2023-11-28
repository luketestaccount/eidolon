"""
We provide two strategies for generating thoughts in the Tree of Thoughts (ToT)
framework to avoid repetition:

These strategies ensure that the language model generates diverse and
non-repeating thoughts, which are crucial for problem-solving tasks that require
exploration.
"""
from abc import abstractmethod
from typing import Any, Dict, List, Callable, Awaitable

from jinja2 import StrictUndefined, Environment
from pydantic import Field, BaseModel

from eidolon_sdk.cpu.llm_message import UserMessageText, UserMessage, LLMMessage, AssistantMessage
from eidolon_sdk.impl.tot_controller.prompts import POST_AMBLE, THOUGHTS, PREAMBLE, POST_AMBLE_MULTI
from eidolon_sdk.reference_model import Specable


class TGSConfig(BaseModel):
    preamble: str = PREAMBLE
    thoughts: str = THOUGHTS
    post_amble: str = POST_AMBLE
    c: int = Field(3, description="The number of thoughts to generate.")


class BaseThoughtGenerationStrategy(Specable[TGSConfig]):
    """
    Base class for a thought generation strategy.
    """
    spec: TGSConfig
    env = Environment(undefined=StrictUndefined)

    def __init__(self, spec):
        self.spec = spec

    def build_prompt(self, user_message, thoughts_path: List[str]):
        thoughts_tuple = tuple(thoughts_path)
        preamble_txt = self.env.from_string(self.spec.preamble).render(thoughts=thoughts_tuple, n=self.spec.c)
        thoughts_txt = self.env.from_string(self.spec.thoughts).render(thoughts=thoughts_tuple, n=self.spec.c)
        post_amble_txt = self.env.from_string(self.spec.post_amble).render(thoughts=thoughts_tuple, n=self.spec.c)
        return [
            UserMessage(content=[UserMessageText(text=preamble_txt)]),
            user_message,
            UserMessage(content=[UserMessageText(text=thoughts_txt)]),
            UserMessage(content=[UserMessageText(text=post_amble_txt)])
        ]

    @abstractmethod
    async def next_thought(
        self,
        user_message: UserMessage,
        llm_call: Callable[[List[LLMMessage], Dict[str, Any]], Awaitable[str]],
        thoughts_path: List[str] = Field(default_factory=list),
    ) -> str:
        """
        Generate the next thought given the problem description and the thoughts
        generated so far.
        """


class SampleCoTStrategyOutput(BaseModel):
    thought: str


class SampleCoTStrategy(BaseThoughtGenerationStrategy):
    """
    Sample thoughts from a Chain-of-Thought (CoT) prompt.

    This strategy works better when the thought space is rich, such as when each
    thought is a paragraph. Independent and identically distributed samples
    lead to diversity, which helps to avoid repetition.
    """

    async def next_thought(
        self,
        user_message: UserMessage,
        llm_call: Callable[[List[LLMMessage], Dict[str, Any]], Awaitable[AssistantMessage]],
        thoughts_path: List[str] = Field(default_factory=list),
    ) -> str:
        messages = self.build_prompt(user_message, thoughts_path)
        next_thought = await llm_call(messages, SampleCoTStrategyOutput.model_json_schema())
        return next_thought.content["thought"]


class ProposeOutputFormat(BaseModel):
    thoughts: List[str]


class ProposePromptStrategyConfig(TGSConfig):
    post_amble: str = POST_AMBLE_MULTI


class ProposePromptStrategy(BaseThoughtGenerationStrategy, Specable[ProposePromptStrategyConfig]):
    """
    Propose thoughts sequentially using a "propose prompt".

    This strategy works better when the thought space is more constrained, such
    as when each thought is just a word or a line. Proposing different thoughts
    in the same prompt completion helps to avoid duplication.
    """
    tot_memory: Dict[tuple, List[str]]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tot_memory = {}

    async def next_thought(
        self,
        user_message: UserMessage,
        llm_call: Callable[[List[LLMMessage], Dict[str, Any]], Awaitable[AssistantMessage]],
        thoughts_path: List[str] = Field(default_factory=list)
    ) -> str:
        thoughts_tuple = tuple(thoughts_path)
        if thoughts_tuple not in self.tot_memory or not self.tot_memory[thoughts_tuple]:
            messages = self.build_prompt(user_message, thoughts_path)
            next_thought_msg = await llm_call(messages, ProposeOutputFormat.model_json_schema())
            self.tot_memory[thoughts_tuple] = next_thought_msg.content["thoughts"]
        return self.tot_memory[thoughts_tuple].pop()