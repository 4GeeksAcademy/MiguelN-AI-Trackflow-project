"""What one subflow hands to the next: kept in memory, recorded by Prefect as row counts.

Prefect stores every subflow run's parameters in its database and shows them
in its UI, JSON-encoded where it can. A DataFrame of text columns encodes, so
passing the week's events or the per-client rows to a subflow as plain
parameters would copy them there: event rows the extract tasks keep out of
result storage (`persist_result=False`), and per-client figures the eval
snapshot deliberately leaves out (they are commercially sensitive).

A stage's output therefore crosses a subflow boundary inside a `StageData`.
The subflow receives the objects unchanged; Prefect records only their shape,
for example `{"events": {"rows": 15234}, ...}`, which is also what an operator
wants to see in the UI. Small metadata (the run handle, the load counts) passes
as plain parameters.
"""

from dataclasses import fields, is_dataclass
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, model_serializer


def summarize(value: Any) -> Any:
    """A DataFrame becomes its row count; a dataclass, its fields summarized; the rest stays."""
    if isinstance(value, pd.DataFrame):
        return {"rows": len(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: summarize(getattr(value, item.name)) for item in fields(value)}
    return value


class StageData(BaseModel):
    """Base for a stage's output. Subclasses declare the fields; Prefect sees row counts."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    @model_serializer
    def _summary(self) -> dict[str, Any]:
        return {name: summarize(getattr(self, name)) for name in type(self).model_fields}
