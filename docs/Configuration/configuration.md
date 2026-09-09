## Registration with NASA Earthdata

The application downloads raw satellite data (i.e., granules) using the __[NASA EarthData](https://www.earthdata.nasa.gov/)__ system via the __[earthaccess](https://earthaccess.readthedocs.io/en/stable/)__ python library.

This requires that the user __[register for a free Earthdata Login](https://urs.earthdata.nasa.gov/)__. 

## Configuring CDSE

As discussed in the __[1. Download Granules](../User%20Guide/Analysis/download_granules.md#download-preview-images)__ stage, the application generates preview images of Sentinel-3A/B requires using the EU's Copernicus Data Space Ecosystem (CDSE) via the Sentinel-Hub pacakge. This requires a separate authentication method than the NASA EarthAccess method.

### CDSE Registration Instructions

!!! note "CDSE Credential Expiration"
    By default, CDSE credentials expire after 90 days. The user may choose to extend this expiration period, but it is preferable to simply create and re-enter new set of credentials when the old credentials expire.

1. Login or Register a new account with the EU's __[Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/)__ 

2. In the sidebar click "User Settings" and in the `OAuth clients` tab click the green `+ Create` button

3. Provide a Client Name (e.g., `<USER>-laptop`), keep all default settings and click the green `+ Create` button.

4. Within the main appliction, open the sidebar and click the blue __`Configure OAuth Credentials`__ button. Copy the `Client ID` and `Client Secret` from the CDSE webpage and click the blue __`Store Credentials`__ button. 

5. A pop-up will verify that the user has entered valid credentials. 