from typing import Tuple
from pyresample import create_area_def
from pyresample.geometry import AreaDefinition
from pyproj import Transformer

def lonlat_to_epsg(lon, lat, epsg):
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y

def create_target_area_def(
    casename: str,
    bounding_box : Tuple[float,float,float,float],
    resolution: Tuple[float,float],
    epsg: float,
    epsg_units: str
    ) -> AreaDefinition:

    lon1, lat1, lon2, lat2 = bounding_box
    x1,y1 = lonlat_to_epsg(lon1,lat1,epsg)
    x2,y2 = lonlat_to_epsg(lon2,lat2,epsg)
    
    area_def = create_area_def(
        area_id = casename,
        projection = epsg,
        area_extent = (x1,y1,x2,y2),
        resolution = resolution,
        units = epsg_units
    )

    return area_def
    