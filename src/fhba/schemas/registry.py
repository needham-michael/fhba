import datetime
from pathlib import Path
from typing import Literal, Optional, Tuple

from pydantic import BaseModel

from fhba.schemas.app_config import AppConfig

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

class FileMetadata(BaseModel):
    raw_granule : Optional[Path] = None
    reproj_granule : Optional[Path] = None
    truecolor_img_path: Optional[Path] = None
    user_pts: Optional[Path] = None
    burnmask: Optional[Path] = None
        
class GranuleManager(BaseModel):
    date: datetime.datetime
    satellite: str
    files : FileMetadata = FileMetadata()

class StatusMetadata(BaseModel):
    """
    Status Flags
    ------------
    is_downloaded : Whether the raw data granule has been downloaded
    is_retained : Whether the raw data granule was retained after processing
    is_processed : Whether the raw granule has had bands extracted and reproj.
    is_user_categorized : Whether the user has categorized burned/unburned points
    is_classified : Whether the classification algorithm(s) has been applied to
        generate a satellite burnmask for this granule
    """
    is_downloaded: bool = False
    is_retained: bool = False
    is_processed: bool = False
    is_user_categorized: bool = False
    is_classified: bool = False
    
class GranuleMetadata(BaseModel):
    """All metadata assoc. with an individual satellite granule on a specific date.

    See StatusMetadata docstring for information regarding various `status` flags
    
    """
    date: datetime.datetime
    satellite: str
    files : FileMetadata = FileMetadata()
    status : StatusMetadata = StatusMetadata()
    
class Registry(BaseModel):
    """Global registry to maintain complete collection of app metadata"""
    app_config: AppConfig

    # Granule metadata stored in nested dict accessed by <year>, <satellite>, and <date>
    granules: dict[str, dict[str, dict[str, GranuleMetadata]] ]

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