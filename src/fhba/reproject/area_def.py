from typing import Tuple
from pyresample import create_area_def
from pyresample.geometry import AreaDefinition
from pyproj import Transformer

def lonlat_to_webmercator(lon, lat):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y

def create_target_area_def(
    casename: str,
    bounding_box : Tuple[float,float,float,float],
    resolution: Tuple[float,float]
    ) -> AreaDefinition:

    lon1, lat1, lon2, lat2 = bounding_box
    x1,y1 = lonlat_to_webmercator(lon1,lat1)
    x2,y2 = lonlat_to_webmercator(lon2,lat2)
    
    area_def = create_area_def(
        area_id = casename,
        projection = 3857,
        area_extent = (x1,y1,x2,y2),
        resolution = resolution,
        units = 'meters'
    )

    return area_def
    