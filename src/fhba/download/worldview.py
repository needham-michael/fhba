import os
import requests

def download_worldview(date,bbox,out_path,overwrite=False,truecolor=True,satellite_name="Suomi-NPP"):
    if os.path.exists(out_path) and not overwrite:
        print("True color image already exists.")
        return True, out_path

    valid_sat_name = {
            'Suomi-NPP':'VIIRS_SNPP',
            'NOAA-20':'VIIRS_NOAA20',
            'NOAA-21':'VIIRS_NOAA21',
            'TERRA':'MODIS_Terra',
            'AQUA':'MODIS_Aqua'
        }

    if satellite_name not in valid_sat_name:
        raise NotImplementedError(f"{satellite_name =} not one of {valid_sat_name.keys()}")

    min_lon, min_lat, max_lon, max_lat = bbox

    url_worldview = r"https://wvs.earthdata.nasa.gov/api/v1/snapshot?REQUEST=GetSnapshot&TIME=DATEPLACEHOLDERT00:00:00Z&BBOX=BBOXPLACEHOLDER&CRS=EPSG:4326&LAYERS=SATNAME_CorrectedReflectance_PRODUCT,Coastlines_15m&WRAP=day,x&FORMAT=image/jpeg&WIDTH=1138&HEIGHT=1820&colormaps=,&ts=1772050098509"

    if truecolor:
        url_worldview = url_worldview.replace("PRODUCT", "TrueColor")
    else:
        if "VIIRS" in valid_sat_name[satellite_name]:
            url_worldview = url_worldview.replace("PRODUCT", "BandsM11-I2-I1")
        elif "MODIS" in valid_sat_name[satellite_name]:
            url_worldview = url_worldview.replace("PRODUCT", "Bands721")

    url_worldview = url_worldview.replace("DATEPLACEHOLDER", date)

    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    url_worldview = url_worldview.replace("BBOXPLACEHOLDER", bbox_str)


    url_worldview = url_worldview.replace("SATNAME", valid_sat_name[satellite_name])

    print(f"{url_worldview = }")

    response = requests.get(url_worldview)
    download_valid = False
    if response.status_code == 200:
        download_valid = True
        with open(out_path, 'wb') as f:
            f.write(response.content)
        print(f"True color image retrieved and saved to {out_path}")
    else:
        print(f"Failed to retrieve true color image. HTTP status code: {response.status_code}")

    return download_valid, out_path
    