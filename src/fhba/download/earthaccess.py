from typing import List, Tuple

import earthaccess
import shapely
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

    # There appears to be a bug in earthaccess where certain granules overlapping
    # the north pole will be inadvertently matched (likely because the longitude
    # and latitude pass through zero). Below is a workaround to ensure that only
    # relevant granules are retained
    #
    # See issue: # https://github.com/earthaccess-dev/earthaccess/issues/1464

    granules_filtered = []
    bounding_box_poly = shapely.Polygon.from_bounds(*bounding_box)

    # Iterate over each granule and only retain granules which intersect 
    # the user's bounding box
    for granule in granule_search_results:
        poly = get_granule_geom(granule)
        if poly.intersects(bounding_box_poly):
            granules_filtered.append(granule)

    return granules_filtered

def get_granule_geom(granule):
    """Extract granule spatial extent from metadata"""
    geom = granule['umm']['SpatialExtent']['HorizontalSpatialDomain']['Geometry']['GPolygons']

    polys = []
    for g in geom:
        polys.append(shapely.Polygon(
            [(pt['Longitude'],pt['Latitude']) for pt in g['Boundary']['Points']]
        ))

    return shapely.union_all(polys)