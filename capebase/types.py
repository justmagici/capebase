from typing import TypeVar
from sqlmodel import SQLModel

# Common TypeVars used across the application
ModelType = TypeVar("ModelType", bound=SQLModel) 