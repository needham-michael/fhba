"""Module to sync pydantic model with registry json file"""
import json
from importlib import resources
from pathlib import Path
from typing import Tuple

from fhba.schemas import Registry, FHBACases

def reg2json(json_registry_filename: Path, registry: Registry) -> None:
    """Dump pydantic Registry model to json file"""
    with open(json_registry_filename,"w",encoding='utf-8') as f:
        json.dump(registry.model_dump(mode='json'),f,indent=2)

def json2reg(json_registry_filename: str | Path) -> Registry:
    """Read json file and validate as pydantic Registry model"""
    with open(json_registry_filename,"r",encoding='utf-8') as f:
        data = json.load(f)

    return Registry.model_validate(data) 

def cases2json(json_cases_filename: Path, fhba_cases) -> None:
    """Dump pydantic FHBACases model to json file"""
    with open(json_cases_filename,"w",encoding='utf-8') as f:
        json.dump(fhba_cases.model_dump(mode='json'),f,indent=2)

def json2cases(json_cases_filename: Path = None) -> Tuple[Path, FHBACases]:
    """Read json file of all app cases; if doesn't exist generate new file"""
    if json_cases_filename is None:
        json_cases_filename = resources.files("fhba").parent.parent / "fhba_cases.json"

    # If first time, write empty .json file with correct schema
    if not json_cases_filename.exists():
        with open(json_cases_filename,"w",encoding='utf-8') as f:
            json.dump(FHBACases(cases={}).model_dump(mode='json'),f,indent=2)

    with open(json_cases_filename,"r",encoding='utf-8') as f:
        data = json.load(f)

    return (json_cases_filename, FHBACases.model_validate(data))