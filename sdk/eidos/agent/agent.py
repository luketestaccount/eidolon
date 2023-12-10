from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass
from typing import List, TypeVar, Generic

from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo, Field

from eidos.cpu.agent_cpu import AgentCPU
from eidos.cpu.conversational_agent_cpu import ConversationalAgentCPU
from eidos.cpu.conversational_logic_unit import ConversationalLogicUnit, ConversationalSpec
from eidos.system.reference_model import Specable, AnnotatedReference
from eidos.util.schema_to_model import schema_to_model


class ProcessContext(BaseModel):
    process_id: str
    callback_url: typing.Optional[str]


class AgentSpec(BaseModel):
    cpu: AnnotatedReference[AgentCPU, ConversationalAgentCPU]
    agent_refs: List[str] = []


class Agent(Specable[AgentSpec]):
    cpu: AgentCPU

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cpu = self.spec.cpu.instantiate()
        if self.spec.agent_refs and hasattr(self.cpu, 'logic_units'):
            self.cpu.logic_units.append(ConversationalLogicUnit(
                processing_unit_locator=self.cpu,
                spec=ConversationalSpec(agents=self.spec.agent_refs))
            )


class CodeAgent:
    pass


def register_program(name: str = None, description: str = None, input_model: callable = None, output_model: callable = None):
    return register_action('UNINITIALIZED', name=name, description=description, input_model=input_model, output_model=output_model)


def register_action(*allowed_states: str, name: str = None, description: str = None, input_model: callable = None, output_model: callable = None):
    if not allowed_states:
        raise ValueError("Must specify at least one valid state")
    if 'terminated' in allowed_states:
        raise ValueError("Action cannot transform terminated state")

    return lambda fn: _add_handler(fn, EidolonHandler(
        name=name or fn.__name__,
        description=description or fn.__doc__,
        allowed_states=list(allowed_states),
        fn=fn,
        input_model_fn=input_model or get_input_model,
        output_model_fn=output_model or get_output_model,
    ))


def _add_handler(fn, handler):
    if not inspect.iscoroutinefunction(fn):
        raise ValueError("Handler must be an async function")
    try:
        handlers = getattr(fn, "eidolon_handlers")
    except AttributeError:
        handlers = []
        setattr(fn, "eidolon_handlers", handlers)
    handlers.append(handler)
    return fn


T = TypeVar('T')


class AgentState(BaseModel, Generic[T]):
    name: str
    data: T


def get_input_model(_obj, handler: EidolonHandler) -> BaseModel:
    sig = inspect.signature(handler.fn).parameters
    hints = typing.get_type_hints(handler.fn, include_extras=True)
    fields = {}
    for param, hint in filter(lambda tu: tu[0] != 'return', hints.items()):
        if hasattr(hint, '__metadata__') and isinstance(hint.__metadata__[0], FieldInfo):
            field: FieldInfo = hint.__metadata__[0]
            if getattr(sig[param].default, "__name__", None) != '_empty':
                field.default = sig[param].default
            fields[param] = (hint.__origin__, field)
        else:
            # _empty default isn't being handled by create_model properly (still optional when it should be required)
            field = Field() if getattr(sig[param].default, "__name__", None) == '_empty' else Field(default=sig[param].default)
            fields[param] = (hint, field)

    input_model = create_model(f'{handler.name.capitalize()}InputModel', **fields)
    return input_model


def get_output_model(_obj, handler: EidolonHandler) -> BaseModel:
    return typing.get_type_hints(handler.fn, include_extras=True).get('return', typing.Any)


def nest_with_fn(get_outer: callable, embed=None):
    def _fn(_obj, handler, schema, name):
        rtn = get_outer(_obj, handler).schema()
        if embed:
            rtn['properties'][embed] = schema
        else:
            rtn['properties'].update(schema)
        return schema_to_model(rtn, model_name=name)
    return _fn


def to_model(*args, schema, name):
    return schema_to_model(schema, model_name=name)


def spec_input_model(get_schema: callable, name=None, transformer: callable = nest_with_fn(get_input_model, 'body')):
    return _spec_model_wrapper(get_schema, name, "InputModel", transformer)


def spec_output_model(get_schema: callable, name=None, transformer: callable = to_model):
    return _spec_model_wrapper(get_schema, name, "OutputModel", transformer)


def _spec_model_wrapper(get_schema: callable, name: str, suffix: str, transformer: callable):
    def _fn(_obj, handler):
        return transformer(_obj, handler, schema=get_schema(_obj.spec), name=name or handler.name.capitalize() + suffix)
    return _fn


@dataclass
class EidolonHandler:
    name: str
    description: str
    allowed_states: List[str]
    fn: callable
    input_model_fn: callable
    output_model_fn: callable

    def is_initializer(self):
        return len(self.allowed_states) == 1 and self.allowed_states[0] == 'UNINITIALIZED'