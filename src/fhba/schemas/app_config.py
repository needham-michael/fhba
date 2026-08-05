
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# -------------------------
# Sub‑models for readability
# -------------------------

class SpatialConfig(BaseModel):
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    spatial_name: str


class TemporalConfig(BaseModel):
    start_month: int
    start_day: int
    end_month: int
    end_day: int


class PathConfig(BaseModel):
    raw_data_dir: Path
    processed_data_dir: Path
    truecolor_img_dir: Path
    userpts_dir: Path
    burnmask_dir: Path
    county_shp: Path

    @model_validator(mode="after")
    def normalize_paths(self):
        """Ensure all paths are Path objects (Pydantic v2 already ensures this),
        but keep this hook available for future normalization."""
        return self


class NLCDConfig(BaseModel):
    nlcd_filedir: Path
    nlcd_filename: str

    @model_validator(mode="after")
    def normalize_paths(self):
        # Force nlcd_filedir into a real Path
        self.nlcd_filedir = Path(self.nlcd_filedir)
        return self


class VIIRSsatConfig(BaseModel):
    short_name_list: List[str]
    cloud_mask_short_name: str


class VIIRSConfig(BaseModel):
    full_band_list: List[str]
    nir_red_band_list: List[str]

    # satellite → VIIRSsatConfig
    Suomi_NPP: Optional[VIIRSsatConfig] = Field(None, alias="Suomi-NPP")
    NOAA_20: Optional[VIIRSsatConfig] = Field(None, alias="NOAA-20")
    NOAA_21: Optional[VIIRSsatConfig] = Field(None, alias="NOAA-21")

    model_config = dict(populate_by_name=True)


class MODISsatConfig(BaseModel):
    short_name_list: List[str]


class MODISConfig(BaseModel):
    full_band_list: List[str]
    AQUA: Optional[MODISsatConfig]
    TERRA: Optional[MODISsatConfig]


# -------------------------
# Main AppConfig model
# -------------------------

class AppConfig(BaseModel):
    spatial: SpatialConfig
    temporal: TemporalConfig
    paths: PathConfig
    nlcd: NLCDConfig
    viirs: VIIRSConfig
    modis: MODISConfig

    # Root directory from which all relative paths are resolved
    root_dir: Path = Field(default=Path("."))

    retain_granules_after_processing: bool = True

    @model_validator(mode="after")
    def apply_root_directory(self):
        """
        Convert all path fields into absolute paths based on root_dir.
        """
        def make_absolute(p: Path) -> Path:
            # Already absolute?
            if p.is_absolute():
                return p
            return self.root_dir / p

        # Update all path fields
        self.paths.raw_data_dir = make_absolute(self.paths.raw_data_dir)
        self.paths.processed_data_dir = make_absolute(self.paths.processed_data_dir)
        self.paths.truecolor_img_dir = make_absolute(self.paths.truecolor_img_dir)
        self.paths.userpts_dir = make_absolute(self.paths.userpts_dir)
        self.paths.burnmask_dir = make_absolute(self.paths.burnmask_dir)
        self.paths.county_shp = make_absolute(self.paths.county_shp)

        # NLCD directory
        self.nlcd.nlcd_filedir = make_absolute(self.nlcd.nlcd_filedir)

        return self

def load_app_config(data: dict, root_dir: Path) -> AppConfig:
    data = {**data, "root_dir": root_dir}
    return AppConfig.model_validate(data)