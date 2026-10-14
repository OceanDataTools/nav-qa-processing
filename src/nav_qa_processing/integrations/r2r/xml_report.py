"""Writes the R2R QA 2.0 XML report for a GNSS fileset.

The structure is that of R2R's current nav reports (tests/fixtures/SP2613_169851_r2r_qa.2.0.xml):
the same elements in the same order, namespaces and stylesheet, so R2R's XSLT renders it. Values
are written and units labelled through `quantities.py`, and a missing value is an empty element
with `xsi:nil="true"`. The catalog sections R2R fills from its database (filesetinfo) come from
the cruise config.

Different from R2R's reports (docs/navmanager-differences.md, X1):
- R2R's `cruise` repeats `<r2r:id>` after `operator_id`; it's written once here.
- The provenance names this package as the process, with its version and the run's time.
- The log lists the run's errors (manifest, reader); R2R's is always empty.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from nav_qa_processing import quantities as q
from nav_qa_processing.integrations.r2r.config import CruiseConfig, Port
from nav_qa_processing.integrations.r2r.rules import R2RResult
from nav_qa_processing.qa import NavQAResult

R2R_NAMESPACE = "https://service.rvdata.us/schema/r2r-2.0"
STYLESHEET = "https://service.rvdata.us/stylesheets/r2r_qac_v2.0.xslt"
# R2R's reports declare these exact values, including the https XSI namespace
XSI_NAMESPACE = "https://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = "http://schema.rvdata.us/2.0 http://schema.rvdata.us/2.0/base.xsd"
SERVICE = "https://service.rvdata.us/info"
# Coordinates of R2R's catalog: ports to 5 decimals, the cruise extent to 7 (about 1 cm)
PORT_DECIMALS = 5
EXTENT_DECIMALS = 7

ET.register_namespace("r2r", R2R_NAMESPACE)
ET.register_namespace("xsi", XSI_NAMESPACE)


def _tag(name: str) -> str:
    return f"{{{R2R_NAMESPACE}}}{name}"


def _sub(parent: ET.Element, tag: str, text=None, /, **attributes) -> ET.Element:
    element = ET.SubElement(parent, _tag(tag), {k: str(v) for k, v in attributes.items()})
    if text is not None:
        element.text = str(text)
    return element


def _value(parent: ET.Element, tag: str, text: str, /, **attributes) -> ET.Element:
    """An element holding a formatted value, nil when it's missing"""
    if text == q.MISSING:
        attributes[f"{{{XSI_NAMESPACE}}}nil"] = "true"
        return _sub(parent, tag, None, **attributes)
    return _sub(parent, tag, text, **attributes)


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _port(parent: ET.Element, name: str, port: Port) -> None:
    element = _sub(parent, name)
    _sub(element, "port_id", port.port_id)
    _sub(element, "port_name", port.name)
    _sub(element, "country_id", port.country_id)
    _sub(element, "state_id", port.state_id)
    _value(element, "longitude", q.SHIP_LONGITUDE.format(port.longitude, PORT_DECIMALS))
    _value(element, "latitude", q.SHIP_LATITUDE.format(port.latitude, PORT_DECIMALS))


def _filesetinfo(root: ET.Element, config: CruiseConfig) -> None:
    filesetinfo = _sub(root, "filesetinfo")
    _sub(filesetinfo, "namespace", "R2R")
    cruise = _sub(filesetinfo, "cruise")
    _sub(cruise, "date_modified", config.date_modified)
    _sub(cruise, "date_issued", config.date_issued)
    _sub(cruise, "id", config.cruise_id)
    _sub(cruise, "cname", config.cruise_name)
    _sub(cruise, "operator_id", config.operator_id)
    _sub(cruise, "vessel_id", config.vessel_id)
    _sub(cruise, "scheduler_id", config.scheduler_id)
    _sub(cruise, "depart_date", config.depart_date.isoformat())
    _port(cruise, "depart_port", config.depart_port)
    _sub(cruise, "arrive_date", config.arrive_date.isoformat())
    _port(cruise, "arrive_port", config.arrive_port)
    extent = _sub(cruise, "extent")
    for edge in ("westernmost", "easternmost", "southernmost", "northernmost"):
        coordinate = (
            q.SHIP_LONGITUDE if edge in ("westernmost", "easternmost") else q.SHIP_LATITUDE
        )
        value = getattr(config.extent, edge) if config.extent else None
        _value(extent, edge, coordinate.format(value, EXTENT_DECIMALS))

    fileset = _sub(filesetinfo, "fileset")
    _sub(fileset, "date_created", config.fileset_date_created)
    _sub(fileset, "type", "data")
    _sub(fileset, "id", config.fileset_id)
    _sub(fileset, "parent_fileset_id")
    _sub(fileset, "vessel_id", config.vessel_id)
    _sub(fileset, "cruise_id", config.cruise_id)
    _sub(fileset, "nav_source", "1")  # the primary GNSS feed
    _sub(fileset, "bathy_source", "0")
    device = _sub(fileset, "device")
    _sub(device, "id", config.device.id)
    _sub(device, "devicetype_id", "gnss")
    _sub(device, "make_id", config.device.make_id)
    _sub(device, "model_id", config.device.model_id)
    _sub(device, "model_name", config.device.model_name)
    _sub(device, "location_name", config.device.location_name)
    _sub(fileset, "fileformat_id", config.format_id)
    _sub(fileset, "fileformat_alias", config.format_alias)
    _sub(fileset, "proclevel", "1")


def _certificate(root: ET.Element, r2r: R2RResult) -> None:
    certificate = _sub(root, "certificate")
    _sub(certificate, "rating", r2r.rating, description=r2r.description)
    infos = _sub(certificate, "infos")
    for info in r2r.infos:
        _value(infos, "info", info.text, name=info.name, uom=info.quantity.unit)
    tests = _sub(certificate, "tests")
    for test in r2r.tests:
        element = _sub(tests, "test", name=test.name, description=test.description)
        _sub(element, "rating", test.rating)
        _value(element, "test_result", test.text, uom=test.quantity.unit)
        bounds = _sub(element, "bounds")
        for bound in test.bounds:
            _sub(bounds, "bound", bound.text, name=bound.name, uom=bound.quantity.unit)


def _supplements(root: ET.Element, result: NavQAResult) -> None:
    s = result.stats
    decimals = result.nav.decimals
    supplements = _sub(root, "filesetinfo_supplements")
    for name, quantity, values, places in (
        ("NSV", q.NSV, s.nsv, None),
        ("HDOP", q.HDOP, s.hdop, decimals.get("hdop", 1)),
        ("Altitude", q.ANTENNA_HEIGHT, s.altitude, decimals.get("alt", 3)),
        ("HorizontalSpeed", q.SPEED_MADE_GOOD, s.speed, None),
        ("HorizontalAcceleration", q.HORIZONTAL_ACCELERATION, s.acceleration, None),
    ):
        supplemental = _sub(supplements, "filesetinfo_supplemental", name=name)
        ranges = _sub(supplemental, "ranges")
        for edge, value in (("Minimum", values.minimum), ("Maximum", values.maximum)):
            _value(ranges, "range", quantity.format(value, places), name=edge, uom=quantity.unit)


def _references(root: ET.Element, config: CruiseConfig) -> None:
    references = _sub(root, "references")
    vessel = f"{SERVICE}/vessel/{config.vessel_id}"
    for available, src, text in (
        (config.vessel_id, vessel, "supporting docs (if any) for this vessel"),
        (
            config.vessel_id and config.device.id,
            f"{vessel}/device/{config.device.id}",
            "supporting docs (if any) for this device instance on this vessel",
        ),
        (
            config.device.model_id,
            f"{SERVICE}/model/{config.device.model_id}",
            "supporting docs (if any) for this device make/model",
        ),
        (
            config.format_id,
            f"{SERVICE}/format/{config.format_id}",
            "supporting docs (if any) for this file format",
        ),
    ):
        if available:
            _sub(references, "reference", text, src=src)


def build_qa_xml(result: NavQAResult, r2r: R2RResult) -> ET.ElementTree:
    config = result.config
    if not isinstance(config, CruiseConfig):
        raise TypeError("the R2R QA XML needs a CruiseConfig (load_cruise_config)")
    root = ET.Element(_tag("qareport"), {f"{{{XSI_NAMESPACE}}}schemaLocation": SCHEMA_LOCATION})
    root.append(
        ET.Comment("Rolling Deck to Repository (R2R) Quality Assessment (QA) Rollup Report")
    )
    _sub(root, "version", "2.0")
    _sub(root, "identifier", f"{config.identifier}_qa")

    provenance = _sub(root, "provenance")
    institution = _sub(provenance, "contact_institution", config.contact_institution)
    if config.contact_institution_id:
        institution.set("id", config.contact_institution_id)
    _sub(provenance, "contact_email", config.contact_email)
    _sub(provenance, "distro_type", config.distro_type)
    _sub(provenance, "cruise_id", config.cruise_id)
    _sub(provenance, "fileset_id", config.fileset_id)
    _sub(provenance, "device_type", "gnss")
    update = _sub(_sub(provenance, "updates"), "update")
    _sub(update, "process", "nav-qa-processing", version=result.versions["nav-qa-processing"])
    _sub(update, "time", _timestamp(result.finished))

    _filesetinfo(root, config)
    _certificate(root, r2r)
    _supplements(root, result)
    _references(root, config)
    log = _sub(root, "log")
    for error in result.errors:
        _sub(log, "entry", error)
    _sub(root, "configuration")
    files = _sub(_sub(root, "manifest"), "files")
    for entry in result.fileset.manifest:
        _sub(files, "file", entry.path, id=entry.file_id, checksum=entry.md5, order="")

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def write_qa_xml(result: NavQAResult, r2r: R2RResult, path: Path | str) -> Path:
    path = Path(path)
    body = ET.tostring(build_qa_xml(result, r2r).getroot(), encoding="unicode")
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?xml-stylesheet type="text/xsl" href="{STYLESHEET}"?>\n' + body + "\n",
        encoding="utf-8",
    )
    return path


def qa_xml_name(config: CruiseConfig, finished: datetime) -> str:
    """R2R's name for the report: <CRUISE>_<FILESET>_qa.2.0.<timestamp>.xml"""
    return f"{config.identifier}_qa.2.0.{_timestamp(finished)}.xml"
