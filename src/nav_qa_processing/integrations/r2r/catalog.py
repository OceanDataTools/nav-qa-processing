"""Cruise configs from R2R's catalog and from R2R's QA 2.0 reports.

What R2R's services give (checked on SP2613, 2026-10-14):
- `api/cruise/?cruise_id=`: name, chief scientist, vessel ICES code and name, operator,
  scheduler, dates, port names and IDs, the cruise DOI and the navigation bounding box.
- `api/fileset/?cruise_id=`: per fileset its device type, device ID, make, model name, R2R
  format ID, DOI and download URL.
- `info/vessel/<ICES>/device/<ID>` (JSON): the device's model ID and location.
- `api/qa_info/?fileset_id=`: the QA report's URL among its `documents`.

Nothing gives the ports' coordinates, country and state, R2R's format alias (`starc_raw`) or
its catalog dates (`date_issued`, `date_modified`, the fileset's `date_created`); R2R's own QA
report has them in its `filesetinfo`. `config_from_r2r_catalog` therefore fills what the API
lacks from that report when R2R has one, so the config reproduces the report's filesetinfo.
"""

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import fields, replace
from pathlib import Path

from nav_qa_processing.config import Extent, as_date
from nav_qa_processing.formats import catalog as formats_catalog
from nav_qa_processing.integrations.r2r.config import CruiseConfig, Device, Port
from nav_qa_processing.integrations.r2r.xml_report import R2R_NAMESPACE
from nav_qa_processing.network import reaching

R2R = "https://service.rvdata.us"
R2R_API = f"{R2R}/api"
R2R_API_TIMEOUT = 60  # [s]
# GeoCSV attribution and repository of R2R's products
R2R_ATTRIBUTION = "Rolling Deck to Repository (R2R) Program; http://www.rvdata.us/"
R2R_REPOSITORY = "doi:10.17616/R39C8D"
R2R_INSTITUTION = ("Rolling Deck to Repository (R2R)", "us.rvdata", "info@rvdata.us")
NS = {"r2r": R2R_NAMESPACE}


class R2RError(LookupError):
    """R2R's catalog doesn't have what was asked for"""


def _get(url: str, service: str = "R2R") -> bytes:
    with reaching(service, url), urllib.request.urlopen(url, timeout=R2R_API_TIMEOUT) as response:
        return response.read()


def fetch_r2r_records(endpoint: str, **query) -> list[dict]:
    """Records from R2R's catalog API (`api/cruise`, `api/fileset`, `api/qa_info`, …). The API
    answers "not found" with a 204 status in the body and no data
    """
    body = _get(f"{R2R_API}/{endpoint}/?{urllib.parse.urlencode(query)}")
    return json.loads(body).get("data") or []


def fetch_device(vessel_id: str, device_id: str) -> dict:
    """R2R's record of a device on a vessel (`info/vessel/<ICES>/device/<ID>`), or {}"""
    url = (
        f"{R2R}/info/vessel/{urllib.parse.quote(vessel_id)}/device/{urllib.parse.quote(device_id)}"
    )
    try:
        return json.loads(_get(url))
    except ValueError:  # R2R answers an unknown device with an HTML page
        return {}


def qa_report_url(fileset_id: int | str) -> str | None:
    """The URL of R2R's QA 2.0 report for a fileset, from `api/qa_info`'s documents"""
    for record in fetch_r2r_records("qa_info", fileset_id=fileset_id):
        for document in json.loads(record.get("documents") or "[]"):
            if re.search(r"_qa\.2\.0\..*\.xml$", document.get("file_name", "")):
                return document["uri"]
    return None


def nav_format(r2r_format_id: int | str) -> str:
    """Our format ID for an R2R format ID (the definitions' `r2r_formats`), or "" """
    for format_id, definition in formats_catalog.formats().items():
        if int(r2r_format_id) in definition.r2r_formats:
            return format_id
    return ""


def _doi(value: str) -> str:
    return f"doi:{value}" if value and not value.startswith("doi:") else value


def config_from_r2r_api(cruise: dict, fileset: dict | None = None, device: dict | None = None):
    """Cruise config from R2R's API records: `api/cruise`, optionally `api/fileset` and the
    `info/vessel/…/device/…` record. R2R's QA reports identify the vessel by its ICES code, which
    the API calls `vessel_ices_code` (its `vessel_id` is the vessel's name)
    """
    fileset = fileset or {}
    device = device or {}

    def value(record, key):
        return str(record.get(key) or "").strip()

    edges = ("longitude_min", "longitude_max", "latitude_min", "latitude_max")
    extent = None
    if all(value(cruise, edge) for edge in edges):
        west, east, south, north = (float(value(cruise, edge)) for edge in edges)
        extent = Extent(west, east, south, north)
    name, institution_id, email = R2R_INSTITUTION
    return CruiseConfig(
        cruise_id=value(cruise, "cruise_id"),
        depart_date=as_date(value(cruise, "depart_date")),
        arrive_date=as_date(value(cruise, "arrive_date")),
        extent=extent,
        cruise_name=value(cruise, "cruise_name"),
        cruise_pi=value(cruise, "chief_scientist"),
        cruise_location=value(cruise, "waterbody_name"),
        cruise_doi=_doi(value(cruise, "cruise_doi")),
        vessel_id=value(cruise, "vessel_ices_code") or value(cruise, "vessel_id"),
        vessel_name=value(cruise, "vessel_name"),
        operator_id=value(cruise, "operator_id"),
        scheduler_id=value(cruise, "scheduler_id"),
        depart_port=Port(
            name=value(cruise, "depart_port_name"), port_id=value(cruise, "depart_port_id")
        ),
        arrive_port=Port(
            name=value(cruise, "arrive_port_name"), port_id=value(cruise, "arrive_port_id")
        ),
        fileset_id=value(fileset, "fileset_id"),
        fileset_doi=_doi(value(fileset, "fileset_doi")),
        format_id=value(fileset, "format_id"),
        nav_format=nav_format(fileset["format_id"]) if fileset.get("format_id") else "",
        device=Device(
            id=value(fileset, "device_id"),
            make_id=value(device, "make_id") or value(fileset, "make_id"),
            model_id=value(device, "model_id"),
            model_name=value(device, "model_name") or value(fileset, "model_name"),
            location_name=value(device, "location_name"),
        ),
        contact_institution=name,
        contact_institution_id=institution_id,
        contact_email=email,
        attribution=R2R_ATTRIBUTION,
    )


def _text(element, tag: str) -> str:
    if element is None:
        return ""
    return (element.findtext(f"r2r:{tag}", "", NS) or "").strip()


def _port(element) -> Port:
    coordinate = {c: _text(element, c) for c in ("longitude", "latitude")}
    return Port(
        name=_text(element, "port_name"),
        port_id=_text(element, "port_id"),
        country_id=_text(element, "country_id"),
        state_id=_text(element, "state_id"),
        **{c: float(v) if v else None for c, v in coordinate.items()},
    )


def config_from_qa_report(qa_xml: Path | str | bytes) -> CruiseConfig:
    """Cruise config from an R2R QA 2.0 report's provenance and filesetinfo"""
    root = ET.fromstring(qa_xml) if isinstance(qa_xml, bytes) else ET.parse(qa_xml).getroot()
    provenance = root.find("r2r:provenance", NS)
    cruise = root.find("r2r:filesetinfo/r2r:cruise", NS)
    fileset = root.find("r2r:filesetinfo/r2r:fileset", NS)
    device = None if fileset is None else fileset.find("r2r:device", NS)
    if not _text(cruise, "depart_date"):
        raise R2RError("the QA report has no cruise information (filesetinfo/cruise is empty)")
    extent = None if cruise is None else cruise.find("r2r:extent", NS)
    edges = [
        _text(extent, e) for e in ("westernmost", "easternmost", "southernmost", "northernmost")
    ]
    institution = None if provenance is None else provenance.find("r2r:contact_institution", NS)
    format_id = _text(fileset, "fileformat_id")
    return CruiseConfig(
        cruise_id=_text(cruise, "id") or _text(provenance, "cruise_id"),
        depart_date=as_date(_text(cruise, "depart_date")),
        arrive_date=as_date(_text(cruise, "arrive_date")),
        extent=Extent(*map(float, edges)) if all(edges) else None,
        cruise_name=_text(cruise, "cname"),
        vessel_id=_text(cruise, "vessel_id"),
        operator_id=_text(cruise, "operator_id"),
        scheduler_id=_text(cruise, "scheduler_id"),
        date_issued=_text(cruise, "date_issued"),
        date_modified=_text(cruise, "date_modified"),
        depart_port=_port(cruise.find("r2r:depart_port", NS)),
        arrive_port=_port(cruise.find("r2r:arrive_port", NS)),
        fileset_id=_text(fileset, "id") or _text(provenance, "fileset_id"),
        fileset_date_created=_text(fileset, "date_created"),
        format_id=format_id,
        format_alias=_text(fileset, "fileformat_alias"),
        nav_format=nav_format(format_id) if format_id else "",
        device=Device(
            id=_text(device, "id"),
            make_id=_text(device, "make_id"),
            model_id=_text(device, "model_id"),
            model_name=_text(device, "model_name"),
            location_name=_text(device, "location_name"),
        ),
        distro_type=_text(provenance, "distro_type") or "post-cruise",
        contact_institution="" if institution is None else (institution.text or "").strip(),
        contact_institution_id="" if institution is None else institution.get("id", ""),
        contact_email=_text(provenance, "contact_email"),
    )


def _empty(value) -> bool:
    return value in ("", None) or (isinstance(value, Port | Device) and value == type(value)())


def fill_missing(config: CruiseConfig, other: CruiseConfig) -> CruiseConfig:
    """`config` with its empty values (and the empty parts of its ports and device) from `other`"""
    changes = {}
    for f in fields(config):
        mine, theirs = getattr(config, f.name), getattr(other, f.name)
        if isinstance(mine, Port | Device):
            parts = {
                p.name: getattr(theirs, p.name)
                for p in fields(mine)
                if _empty(getattr(mine, p.name)) and not _empty(getattr(theirs, p.name))
            }
            if parts:
                changes[f.name] = replace(mine, **parts)
        elif _empty(mine) and not _empty(theirs):
            changes[f.name] = theirs
    return replace(config, **changes)


def config_from_r2r_catalog(
    cruise_id: str, fileset_id: int | str, use_qa_report: bool = True
) -> CruiseConfig:
    """Cruise config from R2R's catalog for one of the cruise's GNSS filesets; what the API
    lacks (port coordinates, format alias, catalog dates) comes from R2R's QA report, if any
    """
    cruises = fetch_r2r_records("cruise", cruise_id=cruise_id)
    if not cruises:
        raise R2RError(f"R2R has no cruise {cruise_id}")
    filesets = fetch_r2r_records("fileset", cruise_id=cruise_id)
    gnss = [f for f in filesets if f.get("device_type") == "gnss"]
    matches = [f for f in gnss if str(f.get("fileset_id")) == str(fileset_id)]
    if not matches:
        others = [f for f in filesets if str(f.get("fileset_id")) == str(fileset_id)]
        problem = (
            f"fileset {fileset_id} is {others[0].get('label')}"
            if others
            else f"has no fileset {fileset_id}"
        )
        listed = ", ".join(f"{f['fileset_id']} ({f.get('label')})" for f in gnss) or "none"
        raise R2RError(f"{cruise_id} {problem}; its GNSS filesets: {listed}")
    cruise, fileset = cruises[0], matches[0]
    vessel = str(cruise.get("vessel_ices_code") or "")
    device = fetch_device(vessel, str(fileset["device_id"])) if fileset.get("device_id") else {}
    config = config_from_r2r_api(cruise, fileset, device)
    if use_qa_report and (url := qa_report_url(fileset_id)):
        try:
            report = config_from_qa_report(_get(url, "R2R's QA report"))
        except (R2RError, ET.ParseError):
            return config  # an older report without filesetinfo, or not XML
        config = fill_missing(config, report)
    return config
