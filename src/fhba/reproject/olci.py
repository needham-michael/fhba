import warnings
import zipfile
from pathlib import Path
from typing import List
from pyresample.geometry import AreaDefinition
from satpy import Scene, find_files_and_readers

def reproject_olci(
    raw_refl_granule : List[Path],
    raw_cmsk_granule : List[Path | None], # Unused placeholder to match viirs/modis pattern
    refl_band_list : List[str],
    cmsk_band_list : List[str],
    target_area_def : AreaDefinition,
    resampler : str = 'nearest'
) -> Scene:

    # Expects OLCI granule to be a zip archive and no raw_cmsk_granule
    if raw_cmsk_granule != []:
        raise NotImplementedError("OLCI Reprojection assumes cloud mask info stored in refl granule")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        filenames = []
        for granule in raw_refl_granule:
            extracted_granule = Path(str(granule).replace(".zip",".SEN3"))
            if not extracted_granule.exists():
                with zipfile.ZipFile(granule, 'r') as zip_ref:
                    zip_ref.extractall(granule.parent)
            filenames += list(extracted_granule.iterdir())
                
    scene = Scene(filenames = [str(f) for f in filenames],reader = "olci_l1b")
    scene.load(refl_band_list + cmsk_band_list)

    for k in scene.keys():
        da_name = k['name']
        da = scene[k]
        scene[da_name] = scene[da_name].chunk({
            'x': da.sizes['x'],  # large contiguous lines
            'y': 4096            # match native chunking
        })

    # Utilize bitwise operator to identify "bright pixels" within the bit field defined as:
    #      any pixel with a TOA reflectance greater than a defined threshold is assumed to be 
    #      cloud contaminated, thick aerosols or haze, bright land surfaces (such as sand, snow 
    #      and ice), or bright water surfaces (such as sea ice or sun glint).
    # For more information see the OLCI Level 1 User Guide:
    # https://user.eumetsat.int/resources/user-guides/sentinel-3-olci-level-1-data-guide
    scene['cloud_mask'] = 1-((scene['quality_flags'] >> 27) & 1)

    return scene.resample(target_area_def,resampler=resampler,rows_per_scan=0)
