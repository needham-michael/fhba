import warnings
from pathlib import Path
from typing import List
from pyresample.geometry import AreaDefinition
from satpy import Scene

def reproject_viirs(
    raw_refl_granule : List[Path],
    raw_cmsk_granule : List[Path],
    refl_band_list : List[str],
    cmsk_band_list : List[str],
    target_area_def : AreaDefinition
) -> Scene:
    
    scene_refl = Scene(raw_refl_granule,reader='viirs_l1b')
    scene_cmsk = Scene(raw_cmsk_granule,reader='viirs_l2')

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scene_refl.load(refl_band_list)
        scene_cmsk.load(cmsk_band_list)

    # Rechunk
    for k in scene_refl.keys():
        da_name = k['name']
        da = scene_refl[k]
        scene_refl[da_name] = scene_refl[da_name].chunk({
            'x': da.sizes['x'],  # large contiguous lines
            'y': 4096            # match native chunking
        })

    scene_refl = scene_refl.resample(target_area_def,resampler='ewa')
    scene_cmsk = scene_cmsk.resample(target_area_def,resampler='ewa',rows_per_scan=16)

    for b in cmsk_band_list:
        scene_refl[b] = scene_cmsk[b]

    return scene_refl