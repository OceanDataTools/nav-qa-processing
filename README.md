# nav-qa-processing

> [!WARNING]
> **Under heavy development.** This project is just starting. It has no working functionality
> yet. Its command line, config format and Python API will change without notice.

A Python port of R2R's [NavManager](https://github.com/rvdata/NavManager), built in the same style
as [sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing). From a research
vessel's raw GPS files and a small cruise config, it will produce:

- the R2R navigation standard products:
  - **NavBestRes** (`<CRUISE>_bestres.r2rnav`): full resolution, with bad fixes flagged.
  - **Nav1Min** (`<CRUISE>_1min.r2rnav`): one position per minute.
  - **NavControl** (`<CRUISE>_control.r2rnav`): Douglas–Peucker control points.
- an **R2R QA 2.0 XML** report with the same tests, ratings and bounds as R2R's
  (completeness, longest gap, records out of sequence, bad quality indicators, speed and
  acceleration limits, ...).
- a **PDF report**, every figure as an **SVG**, and a **Jupyter notebook** that re-runs the
  assessment.

It will run from the command line or as an [OpenVDM](https://github.com/OceanDataTools/openvdm)
post-transfer hook.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync
uv run pytest
```

## Development

```bash
uv run pytest                                   # all tests
uv run pytest tests/test_cli.py::test_version   # one test
uv run ruff check . && uv run ruff format --check .
```

## License

[MIT](LICENSE.md)
