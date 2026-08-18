"""Module holding basic configuration data for satellites"""
from typing import Dict

from fhba.schemas.registry import SatelliteSpec

VIIRS_MOD_BANDS = [f"M{b:0>2}" for b in range(1,17)]
VIIRS_IMG_BANDS = [f"I{b:0>2}" for b in range(1,6)]
VIIRS_BANDS_ALL = VIIRS_IMG_BANDS + VIIRS_MOD_BANDS
VIIRS_BANDS_DEFAULT = ["I01","I02","I03","M03","M04","M05","M07","M08","M11"]

SATCONFIG: Dict[str,Dict] = {
    "Suomi-NPP VIIRS" : {
        "refl_short_name_list": ["VNP02IMG", "VNP03IMG", "VNP02MOD", "VNP03MOD"],
        "cmsk_short_name_list": ["CLDMSK_L2_VIIRS_SNPP"],
        "band_list_all" : VIIRS_BANDS_ALL,
        "band_list_default" : VIIRS_BANDS_DEFAULT,
        "instrument": "viirs",
        "platform":'suomi-npp',
        "start_date":"2012-01-19",
        "end_date":"2026-11-01",
        "access_method": "earthaccess"
    },
    "NOAA-20 VIIRS" : {
        "refl_short_name_list": ["VJ102IMG", "VJ103IMG", "VJ102MOD", "VJ103MOD"],
        "cmsk_short_name_list": ["CLDMSK_L2_VIIRS_NOAA20"],
        "band_list_all" : VIIRS_BANDS_ALL,
        "band_list_default" : VIIRS_BANDS_DEFAULT,
        "instrument": "viirs",
        "platform":'noaa-20',
        "start_date":"2018-01-05",
        "end_date":None,
        "access_method": "earthaccess"
    },
    "NOAA-21 VIIRS" : {
        "refl_short_name_list": ["VJ202IMG", "VJ203IMG", "VJ202MOD", "VJ203MOD"],
        "cmsk_short_name_list": ["CLDMSK_L2_VIIRS_NOAA21"],
        "band_list_all" : VIIRS_BANDS_ALL,
        "band_list_default" : VIIRS_BANDS_DEFAULT,
        "instrument": "viirs",
        "platform":'noaa-21',
        "start_date":"2023-02-10",
        "end_date":None,
        "access_method": "earthaccess"
    },
    # PLACEHOLDER FOR UPCOMING JPSS-4 LAUNCH IN 2027; WILL BE RENAMED NOAA-22
    # "NOAA-22 VIIRS" : {
    #     "refl_short_name_list": [],
    #     "cmsk_short_name_list": [],
    #     "instrument": "viirs",
    #     "platform":'noaa-22',
    #     "start_date":None,
    #     "end_date":None
    #     "access_method": "earthaccess"
    # },
}

def _get_satellite_spec(name: str) -> SatelliteSpec:
    try:
        return SatelliteSpec.model_validate(SATCONFIG[name])
    except KeyError:
        raise ValueError(f"Unknown satellite '{name}'. Available: {list(SATCONFIG)}")

def get_sat_info() -> Dict[str, SatelliteSpec]:
    return {s: _get_satellite_spec(s) for s in SATCONFIG}
