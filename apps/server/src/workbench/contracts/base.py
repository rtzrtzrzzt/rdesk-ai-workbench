from pydantic import BaseModel, ConfigDict


class PublicResponse(BaseModel):
    # An explicit allowlist keeps new private service fields out of HTTP responses.
    model_config = ConfigDict(extra="ignore")
