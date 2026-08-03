import os
import requests
import json

def get_drive_export_task(ee, image, description, folder, filename, region, scale, crs, max_pixels=1e13):
    return ee.batch.Export.image.toDrive(
        image=image,
        description=description,
        folder=folder,
        fileNamePrefix=filename,
        region=region,
        scale=scale,
        crs=crs,
        maxPixels=max_pixels,
        fileFormat='GeoTIFF'
    )

def download_image_local(ee, image, filename, region, scale, crs, feedback):
    import urllib.request
    try:
        url = image.getDownloadURL({
            'name': filename,
            'scale': scale,
            'crs': crs,
            'region': region,
            'format': 'GEO_TIFF'
        })
        feedback.pushInfo(f"Descargando desde: {url}")
        urllib.request.urlretrieve(url, filename)  # nosec
        return True
    except Exception as e:
        feedback.reportError(f"Error descargando imagen: {str(e)}")
        return False
