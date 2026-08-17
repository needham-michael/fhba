import datetime
import json
from pathlib import Path
from typing import List, Literal, Optional, Tuple, Union

from pydantic import BaseModel

# from fhba.schemas.app_config import AppConfig

class AreaDefSpec(BaseModel):
    """
    JSON‑serializable spec for reconstructing a pyresample/satpy area definition.
    """
    projection: int | dict = 3857               # EPSG or PROJ dict
    width: int = 500
    height: int = 1000
    area_extent: Tuple[float, float, float, float]
    units: Literal["degrees", "meters"] = "degrees"

    def to_pyresample(self):
        from pyresample import create_area_def
        return create_area_def(
            area_id="custom",
            projection=self.projection,
            width=self.width,
            height=self.height,
            area_extent=self.area_extent,
            units=self.units,
        )

class SatelliteSpec(BaseModel):
    refl_short_name_list : List[str]
    cmsk_short_name_list : List[str]
    instrument: str  # e.g., MODIS or VIIRS
    platform: str    # e.g., AQUA or Suomi-NPP
    start_date: str | None = None
    end_date: str | None = None
    access_method: str = 'earthaccess' # download via (e.g., earthaccess)

class FileMetadata(BaseModel):
    raw_refl_granule : Optional[List[Path]] = None
    raw_cmsk_granule : Optional[List[Path]] = None
    reproj_granule : Optional[Path] = None
    truecolor_img_path: Optional[Path] = None
    user_pts: Optional[Path] = None
    burnmask: Optional[Path] = None
    
class GranuleManager(BaseModel):
    """All metadata assoc. with an individual satellite granule on a specific date.

    Status Flags
    ------------
    
    is_unavailable : Whether the data was unavailable for the date
    is_downloaded : Whether the raw data granule has been downloaded
    is_retained : Whether the raw data granule was retained after processing
    is_processed : Whether the raw granule has had bands extracted and reproj.
    is_user_categorized : Whether the user has categorized burned/unburned points
    is_classified : Whether the classification algorithm(s) has been applied to
        generate a satellite burnmask for this granule
    
    """
    date: datetime.datetime
    satellite: str
    files : FileMetadata = FileMetadata()
    selected_granule : Union[List[str], str, None] = None
    blend_method : str | None = "Stack"
    categorization: str = "Uncategorized"
    is_unavailable: bool = False
    is_downloaded: bool = False
    is_retained: bool = False
    is_processed: bool = False
    is_user_categorized: bool = False
    is_classified: bool = False
    
class Registry(BaseModel):
    """Case registry to maintain complete collection of metadata for case"""
    casename: str
    bounding_box: Tuple[float,float,float,float]
    county_shp: Path
    caseroot: Path
    output_root: Path
    dataroot: Path
    path_lmask: Path
    path_burnmask: Path
    path_wldv: Path
    path_raw: Path
    path_processed: Path
    path_usrpt: Path
    path_burnmask_final: Path
    json_filename: Path

    # Granule metadata stored in nested dict accessed by <year>, <satellite>, and <date>
    granules: dict[str, dict[str, dict[str, GranuleManager]] ] = {}
    sat_info: dict[str, SatelliteSpec] = None
    area_def_spec: Optional[AreaDefSpec] = None

    def define_satpy_area_def(self, width: int = 500, height: int = 1000):
        import warnings
        warnings.warn("Using hardcoded parameters for registry.satpy_area_def")
        warnings.warn("Projection set to Web Mercator (EPSG:3857) for registry.satpy_area_def.")
        if self.spatial is None:
            self.spatial = (self.app_config.min_lon, self.app_config.min_lat, self.app_config.max_lon, self.app_config.max_lat)
        self.area_def_spec = AreaDefSpec(
            projection=3857, width=width, height=height, area_extent=self.spatial, units="degrees"
        )
        self._satpy_area_def = self.area_def_spec.to_pyresample()

    def to_json(self) -> None:
        with open(self.json_filename,"w",encoding='utf-8') as f:
            json.dump(self.model_dump(mode='json'),f,indent=2)