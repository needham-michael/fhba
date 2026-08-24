import json
from importlib import resources
from pathlib import Path

from fhba.schemas import Registry
from fhba.schemas.sat_config import get_sat_info
from fhba.landcover import preprocess_nlcd
from fhba.reproject import write_raster

def create_case_registry(casename,path_data,path_output,bbox,dx,dy):
    case_registry_filename = path_data / f"fhba_{casename}.json"

    caseroot = path_data
    dataroot = caseroot / "data"

    fhba_dirs = dict(
        caseroot = path_data,
        dataroot = dataroot,
        path_lmask_dir = dataroot / "landmask",
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

def create_case_landcover_mask(registry : Registry, nlcd_file_fullres: Path) -> None:
    grassland_pasture_mask, openwater_mask, target_area_def = preprocess_nlcd(
        registry,nlcd_file_fullres,compute=True)

    nlcd_mask = (grassland_pasture_mask * openwater_mask).rename("nlcd_lcmask")

    nlcd_output_filename = registry.path_lmask_dir / f"nlcd_lcmask_{registry.casename}.tif"

    write_raster(raster=nlcd_mask,output_filename=nlcd_output_filename,target_area_def=target_area_def)

    registry.path_lmask = nlcd_output_filename

    registry.to_json()
    


