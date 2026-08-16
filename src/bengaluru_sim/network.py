from __future__ import annotations

import hashlib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .commands import find_sumo_binary, run_sumo_command
from .config import SimulationConfig


OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_osm(config: SimulationConfig) -> Path:
    """Download and cache all OSM highway ways inside the configured bbox."""
    target = config.osm_file
    bbox_text = ",".join(map(str, config.bbox))
    cached_bbox = (
        config.osm_bbox_file.read_text(encoding="utf-8").strip()
        if config.osm_bbox_file.exists()
        else None
    )
    if target.exists() and cached_bbox == bbox_text and not config.refresh_network:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = config.bbox
    query = (
        "[out:xml][timeout:180];"
        f'(way["highway"]({south},{west},{north},{east});>;);'
        "out body;"
    )
    payload = urllib.parse.urlencode({"data": query}).encode()
    errors: list[str] = []

    for endpoint in OVERPASS_ENDPOINTS:
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"User-Agent": "bengaluru-shield-sim/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                data = response.read()
            if not data.lstrip().startswith(b"<?xml"):
                raise RuntimeError("response was not OSM XML")
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
            config.osm_bbox_file.write_text(bbox_text + "\n", encoding="utf-8")
            return target
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            errors.append(f"{endpoint}: {error}")

    raise RuntimeError("Could not download OSM data:\n" + "\n".join(errors))


def build_network(config: SimulationConfig) -> Path:
    """Convert the cached OSM extract into a passenger-car SUMO network."""
    osm_file = download_osm(config)
    network_file = config.network_file
    if (
        network_file.exists()
        and not config.refresh_network
        and network_file.stat().st_mtime >= osm_file.stat().st_mtime
    ):
        return network_file

    network_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        find_sumo_binary("netconvert"),
        "--osm-files",
        str(osm_file),
        "--output-file",
        str(network_file),
        "--lefthand",
        "true",
        "--keep-edges.by-vclass",
        "passenger",
        "--keep-edges.components",
        "1",
        "--remove-edges.isolated",
        "true",
        "--geometry.remove",
        "true",
        "--ramps.guess",
        "true",
        "--roundabouts.guess",
        "true",
        "--junctions.join",
        "true",
        "--tls.guess-signals",
        "true",
        "--tls.discard-simple",
        "true",
        "--tls.join",
        "true",
        "--tls.default-type",
        "actuated",
        "--output.street-names",
        "true",
        "--seed",
        str(config.seed),
    ]
    run_sumo_command(command, network_file.parent / "netconvert.log")
    return network_file
