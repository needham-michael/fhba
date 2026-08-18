import json
from importlib import resources
from fhba.schemas import Registry
from fhba.schemas.sat_config import get_sat_info

def create_case_registry(casename,path_data,path_output,bbox,dx,dy):
    case_registry_filename = path_data / f"fhba_{casename}.json"

    caseroot = path_data
    dataroot = caseroot / "data"

    fhba_dirs = dict(
        caseroot = path_data,
        dataroot = dataroot,
        path_lmask = dataroot / "landmask",
        path_wldv = dataroot / "worldview",
        path_raw = dataroot / "raw",
        path_processed = dataroot / "processed",
        path_usrpt = dataroot / "userpts",
        output_root = path_output,
        path_burnmask = path_output / "burnmask",
        path_burnmask_final = path_output / "burnmask_final",
    )

    for k in fhba_dirs:
        if k != "caseroot" and k != "output_root":
            fhba_dirs[k].mkdir()

    county_shp = resources.files("fhba._static") / "FH_Counties_Updated.shp"

    case_registry = Registry(
        casename = casename,
        bounding_box = bbox,
        county_shp = county_shp,
        **fhba_dirs,
        json_filename=case_registry_filename,
        sat_info=get_sat_info(),
        sat_band_defaults={},
        resolution=(dx,dy)
    )

    with open(case_registry_filename,"w",encoding='utf-8') as f:
        json.dump(case_registry.model_dump(mode='json'),f,indent=2)

    return case_registry, case_registry_filename


