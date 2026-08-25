from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for persisted contracts: reject silent schema drift."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)
