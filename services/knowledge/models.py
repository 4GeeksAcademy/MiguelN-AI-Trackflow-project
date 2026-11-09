"""Request and response contracts for `POST /knowledge/query`."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

MAX_QUESTION_LENGTH = 1000

Question = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH),
]


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question


class KnowledgeAnswer(BaseModel):
    """The model-generated answer only: no chunks, sources list or scores."""

    model_config = ConfigDict(extra="forbid")

    answer: str
