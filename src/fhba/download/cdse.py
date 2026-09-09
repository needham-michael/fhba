import os
from pathlib import Path
from typing import Tuple
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session
from sentinelhub import SHConfig

import cartopy.crs as ccrs
import geopandas as gpd
import pandas as pd
import shapely

def initialize_cdse_client(credentials: dict | None = None,persist: bool = True, profile="default-profile") -> Tuple:
    """Initialize a CDSE OAuth client from cached or new credentials

    Cached credentials will be read from ~/.config/sentinelhub/config.toml, and new 
    credentials will be saved to the same file if `persist=True`
    
    Credentials for a new OAuth client can be initialized via Copernicus DataSpace and
    should be passed as a dict with the following parameters:

        client_id : str
        client_secret : str

    https://shapps.dataspace.copernicus.eu/dashboard/#/
    Dashboard -> User settings -> OAuth clients -> +Create
    """
    from oauthlib.oauth2.rfc6749.errors import InvalidClientError
    if credentials:
        config = SHConfig(
            sh_client_id = credentials["client_id"],
            sh_client_secret = credentials["client_secret"],
            sh_base_url = "https://sh.dataspace.copernicus.eu",
            sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        )
        if persist:
            config.save(profile=profile)
    else:
        config = SHConfig(profile=profile)
    
    client = BackendApplicationClient(client_id=config.sh_client_id)
    oauth = OAuth2Session(
        client=client,
        )

    try:
        token = oauth.fetch_token(
            token_url='https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token',
            client_secret=config.sh_client_secret, 
            include_client_id=True 
        )
    except InvalidClientError:
        return None, None
    return config, oauth

def request_and_filter_features(date : str,bbox : list, platform : str, oauth):
    
    # Identify all features intersecting the bounding box on the selected date
    request = {
      "collections": [
        "sentinel-3-olci"
      ],
      "datetime": f"{date}T00:00:00Z/{date}T23:59:59Z",
      "bbox": bbox,
      "limit": 10
    }

    response = oauth.post(url="https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search", json=request)
    if response.status_code == 200:
        features = response.json()['features']

    # Store features within gdf and determine overlap with bounding box
    bbox_gdf = gpd.GeoDataFrame(geometry=[shapely.Polygon.from_bounds(*bbox)],crs=ccrs.PlateCarree())
    features_gdf = gpd.GeoDataFrame(
        data={
            'feat_id':[f['id'] for f in features],
            'datetime':[f['properties']['datetime'] for f in features],
        },
        geometry=[shapely.Polygon(f['geometry']['coordinates'][0]) for f in features],
        crs=ccrs.PlateCarree()
    )
    features_gdf['platform'] = features_gdf['feat_id'].str[:3]
    features_gdf['area'] = gpd.overlay(features_gdf, bbox_gdf, how='intersection').geometry.area

    # Filter features to identify the feature that most completely covers
    # the bounding box for the selected platform
    features_gdf = features_gdf[features_gdf['platform'] == platform].sort_values('area',ascending=False)

    try:
        print(f"{features_gdf = }")
        return dict(features_gdf.sort_values('area',ascending=False).iloc[0])
    except:
        return None

def get_olci_truecolor_evalscript():
    evalscript = """
        //VERSION=3
        function setup() {
          return {
            input: ["B08", "B06", "B04"],
            output: {
              bands: 3,
              sampleType: "AUTO", // default value - scales the output values from [0,1] to [0,255].
            },
          }
        }
        
        function evaluatePixel(sample) {
          return [2.5 * sample.B08, 2.5 * sample.B06, 2.5 * sample.B04]
        }
        """

    return evalscript

def get_olci_truecolor_request(bbox,datetime,nx,ny):
    # Ensure datetime object is iterable
    try: 
        iter(datetime)
    except:
        datetime = [datetime]

    data = []
    for dt in datetime:
        print(dt)
        dt_start = (dt - pd.to_timedelta("5s")).strftime("%Y-%m-%dT%H:%M:%SZ")
        dt_end = (dt + pd.to_timedelta("5s")).strftime("%Y-%m-%dT%H:%M:%SZ")

        data.append({
            "type": "sentinel-3-olci",
            "dataFilter": {
                "timeRange": {
                    "from": dt_start,
                    "to": dt_end,
                },
            },
        })
        
    request = {
        "input": {
            "bounds": {
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                "bbox": bbox,
            },
            "data": data,
        },
        "output": {
            "width": nx,
            "height": ny,
            "responses": [{"format": {"type": "image/jpeg"}}],
        },
        "evalscript": get_olci_truecolor_evalscript(),
    }

    return request

def download_cdse(
        date : str, bbox : Tuple, nx : int, ny : int, out_path : Path, 
        oauth : OAuth2Session,overwrite : bool=False, satellite_name : str="S3A"
    ) -> Tuple[bool, Path]:
    """Download truecolor Sentinel-3 imagery for a bbox to a jpg file"""

    if os.path.exists(out_path) and not overwrite:
        print("True color image already exists.")
        return True, out_path

    feat = request_and_filter_features(
        date=date,bbox=bbox,platform=satellite_name,oauth=oauth)

    if not feat:
        download_valid = False
        return download_valid, out_path


    request = get_olci_truecolor_request(
        bbox=bbox,nx=nx,ny=ny,
        datetime=pd.to_datetime(feat['datetime'])
    )

    response = oauth.post(url="https://sh.dataspace.copernicus.eu/process/v1", json=request)

    download_valid = False
    if response.status_code == 200:
        download_valid = True
        with open(out_path, 'wb') as f:
            f.write(response.content)
        print(f"True color image retrieved and saved to {out_path}")
    else:
        print(f"Failed to retrieve true color image. HTTP status code: {response.status_code}")

    return download_valid, out_path
        