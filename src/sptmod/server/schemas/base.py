"""The base model every contract model derives from."""

from pydantic import BaseModel, ConfigDict


class CoreModel(BaseModel):
    """Base of every contract model; reads its values off the core dataclasses."""

    model_config = ConfigDict(from_attributes=True)

    def service_kwargs(self) -> dict[str, object]:
        """Return the request as the keyword arguments of its service function."""
        return self.model_dump(exclude={"operation_id"})
