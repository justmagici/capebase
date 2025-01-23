import logging
from sqlmodel import SQLModel
from sqlalchemy import inspect as sqlalchemy_inspect

logger = logging.getLogger(__name__)

def get_original_state(obj: SQLModel) -> SQLModel:
    """Helper function to get the original state of an object before changes.
    
    Args:
        obj: The SQLModel object to inspect
        
    Returns:
        A new instance of the same class with the original state
        
    Note:
        This uses SQLAlchemy's inspection system to get the history of changes
        and reconstruct the original state of the object.
    """
    insp = sqlalchemy_inspect(obj)
    if not insp:
        logger.warning(f"Can't inspect object {obj} to get original state")
        return obj  # Return original object if can't inspect

    original_state = {}
    for attr in insp.attrs:
        history = attr.history
        if history.deleted:
            original_state[attr.key] = history.deleted[0]
        else:
            original_state[attr.key] = getattr(obj, attr.key)
    return type(obj)(**original_state)
