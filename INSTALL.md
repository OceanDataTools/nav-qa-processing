# Installing nav-qa-processing

nav-qa-processing needs **Python 3.11 or later**, [uv](https://docs.astral.sh/uv/) and git. Its
compiled dependencies (cartopy, pyproj, shapely, numpy) install as binary wheels on Linux
(x86_64 and aarch64), macOS and Windows, so no system GEOS, PROJ or compiler is needed.

- [macOS](#macos)
- [Debian and Ubuntu](#debian-and-ubuntu)
- [RHEL, AlmaLinux and Rocky Linux](#rhel-almalinux-and-rocky-linux)
- [Windows](#windows)
- [Install for the OpenVDM hook](#install-for-the-openvdm-hook)
- [Check the install](#check-the-install)
- [Map data](#map-data)
- [Proxies and ships without internet](#proxies-and-ships-without-internet)
- [Updating](#updating)

The Linux commands below were run in clean containers (Debian 12, Ubuntu 22.04 and 24.04,
AlmaLinux 8, Rocky Linux 9; aarch64, and x86_64 for Debian and AlmaLinux) through to a QA run on
a committed fileset.

## macOS

```bash
brew install uv                       # git comes with the Xcode command line tools
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync
uv run pytest
```

## Debian and Ubuntu

| Release | System Python | Use |
|---|---|---|
| Debian 12 | 3.11 | the system Python |
| Ubuntu 24.04 LTS | 3.12 | the system Python |
| Ubuntu 22.04 LTS | 3.10, too old | a Python installed by uv |

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates python3    # python3: not needed on 22.04
curl -LsSf https://astral.sh/uv/install.sh | sh             # uv in ~/.local/bin
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync                          # Ubuntu 22.04: uv python install 3.12 && uv sync --python 3.12
uv run pytest
```

## RHEL, AlmaLinux and Rocky Linux

Releases 8 and 9. Their default Python (3.6 or 3.9) is too old, but both have a `python3.11`
package. curl comes installed: on 9, `curl-minimal` provides it, and asking for `curl` conflicts
with it.

```bash
sudo dnf install -y git-core python3.11
curl -LsSf https://astral.sh/uv/install.sh | sh             # uv in ~/.local/bin
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync
uv run pytest
```

## Windows

The core and R2R commands run on Windows (CI tests them there); the OpenVDM hook runs where
OpenVDM does, on Linux. In PowerShell, with [Git for Windows](https://git-scm.com/download/win):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync
uv run pytest
```

`uv run pytest` runs the whole suite (about 10 minutes) on the reference filesets committed in
`tests/data/`; it downloads nothing.

## Install for the OpenVDM hook

Install it where OpenVDM's workers can run it, in its own environment (not OpenVDM's venv), with
the `openvdm` extra (pyyaml, to read `openvdm.yaml`). Put uv where every user finds it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
sudo mkdir -p /opt/nav-qa-processing && sudo chown "$USER": /opt/nav-qa-processing
git clone https://github.com/OceanDataTools/nav-qa-processing.git /opt/nav-qa-processing
cd /opt/nav-qa-processing
uv sync --no-dev --extra openvdm --no-managed-python   # the system Python 3.11+
```

On **Ubuntu 22.04**, whose Python is too old, install a Python with uv, kept beside the install
so it doesn't depend on your home directory:

```bash
export UV_PYTHON_INSTALL_DIR=/opt/nav-qa-processing/python
uv python install 3.12
uv sync --no-dev --extra openvdm --python 3.12
```

Then fetch the map data **as the user OpenVDM's workers run as**, since cartopy caches it in that
user's home. OpenVDM's installer runs every worker as root (supervisor's `user=root`), so:

```bash
sudo /opt/nav-qa-processing/.venv/bin/nav-qa-processing fetch-map-data
# workers run as another user: sudo -u <worker> /opt/nav-qa-processing/.venv/bin/...
```

Leave out `--no-dev` to use the hook's `--execute-notebook` (it needs nbclient and ipykernel).
The rest, the extra directory, the site TOML and the hook in `openvdm.yaml`, is in the
[README's OpenVDM section](README.md#openvdm).

## Check the install

```bash
.venv/bin/nav-qa-processing --version
.venv/bin/nav-qa-processing formats              # the format IDs and which can be read
```

To run the QA on a real fileset, unpack one of the committed references (needs `xz`:
`xz-utils` on Debian and Ubuntu, `xz` on RHEL):

```bash
tar -xJf tests/data/BH19-30_136141_gnss.tar.xz -C /tmp
.venv/bin/nav-qa-processing run /tmp/BH19-30_136141_gnss -c configs/BH19-30_136141.toml -o /tmp/navqa
```

It prints 9097 fixes (nav31) from 2 files and the checks, and writes the GeoCSV products, plots,
PDF and notebook into `/tmp/navqa`.

## Map data

The trackline map's coastlines and the "Over water" check use Natural Earth's land, lakes,
coastline and river layers, which cartopy downloads on first use. `fetch-map-data` downloads
them all at once (about 27 MB to download, 32 MB on disk) into cartopy's data directory, `~/.local/share/cartopy`
(`$XDG_DATA_HOME/cartopy` when that's set). Without them, the maps have no coastlines and the
over-water check is n/a; nothing else changes.

## Proxies and ships without internet

- **A proxy:** uv and `fetch-map-data` use the usual `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY`
  variables. Keep them for `sudo` with `sudo -E` (or `sudo env HTTPS_PROXY=... ...`).
- **Install in port.** The install needs PyPI (packages) and, for `fetch-map-data`, Natural
  Earth's servers; after that the QA needs no network. R2R's commands (`r2r fetch-fileset`,
  `r2r config-from-catalog`) need R2R's servers whenever they run.
- **No connection at all:** install on a connected machine with the same OS release and
  architecture, at the same path, then copy across `/opt/nav-qa-processing` (with its `.venv`,
  and `python/` on Ubuntu 22.04) and the map data directory (`~/.local/share/cartopy` of the
  user who'll run it). A venv only works at the path it was made in.

## Updating

```bash
cd /opt/nav-qa-processing
git pull
uv sync --no-dev --extra openvdm --no-managed-python   # the same options as the install
```

`uv sync` installs exactly the versions in `uv.lock`, so every install of a commit runs the same
packages.
