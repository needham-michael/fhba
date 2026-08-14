from typing import List, Tuple

import earthaccess
from fhba.schemas import SatelliteSpec


def earthaccess_search_granules(
        date : str, 
        bounding_box: Tuple, 
        sat_info : SatelliteSpec,
        search_cloudmask : bool = False,
        ) -> List[earthaccess.DataGranule]:

    search_kwargs = dict(
        short_name=sat_info.refl_short_name_list,
        bounding_box=bounding_box, # A tuple representing spatial bounds in the form (lower_left_lon, lower_left_lat, upper_right_lon, upper_right_lat)
        temporal=(date,date),
        day_night_flag='day',
        instrument=sat_info.instrument.upper(),
        platform=sat_info.platform.upper()   
    )

    if search_cloudmask:
        search_kwargs["short_name"] = sat_info.cmsk_short_name_list

    granule_search_results = earthaccess.search_data(**search_kwargs)

    return granule_search_results