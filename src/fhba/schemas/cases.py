from pathlib import Path
from typing import Dict
from pydantic import BaseModel

class FHBACases(BaseModel):
    """
    JSON‑serializable spec for reconstructing collection of user-created cases
    """
    cases: Dict[str, Path] = {}