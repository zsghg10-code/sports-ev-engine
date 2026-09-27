
from __future__ import annotations
import requests

def hourly(lat: float, lon: float, date: str):
    params={
        "latitude":lat,"longitude":lon,
        "hourly":"temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m",
        "start_date":date,"end_date":date,
        "timezone":"auto",
    }
    r=requests.get("https://api.open-meteo.com/v1/forecast",params=params,timeout=20)
    r.raise_for_status()
    return r.json()
