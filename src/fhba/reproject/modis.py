import warnings
from pathlib import Path
from typing import List
from pyresample.geometry import AreaDefinition
from satpy import Scene

def reproject_modis(
    raw_refl_granule : List[Path],
    raw_cmsk_granule : List[Path],
    refl_band_list : List[str],
    cmsk_band_list : List[str],
    target_area_def : AreaDefinition
) -> Scene:

    raw_refl_granule = [str(f) for f in raw_refl_granule if ".hdf" in str(f)]
    raw_cmsk_granule = [str(f) for f in raw_cmsk_granule if ".hdf" in str(f)]

    print(f"{raw_refl_granule = }")
    scene_refl = Scene(raw_refl_granule,reader='modis_l1b')
    print(f"{raw_cmsk_granule = }")
    scene_cmsk = Scene(raw_cmsk_granule,reader='modis_l2')

    print(f"{refl_band_list = }")
    scene_refl.load(refl_band_list)
    print(f"{cmsk_band_list = }")
    scene_cmsk.load(cmsk_band_list,resolution=1000) # Specify 1km cloud mask

    # Convert the quality flag (0-3) to binary cloud mask, allowing 
    # pixels that are "probably" or "confidently" clear.
    # https://atmosphere-imager.gsfc.nasa.gov/products/cloud-mask/format-content
    scene_cmsk['cloud_mask'] = (scene_cmsk['cloud_mask'] >= 2).astype(float)

    scene_refl = scene_refl.resample(target_area_def,resampler='ewa')
    scene_cmsk = scene_cmsk.resample(target_area_def,resampler='ewa')

    for b in cmsk_band_list:
        scene_refl[b] = scene_cmsk[b]

    return scene_refl