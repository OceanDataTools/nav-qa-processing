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

[OpenVDM](https://github.com/OceanDataTools/openvdm) runs the QA after each transfer of the
collection system that logs the ship's GNSS, as a `postCollectionSystemTransfer` hook. Setting
it up takes five steps: install, add an extra directory for the reports, write the ship's site
TOML, add the hook to `openvdm.yaml`, and try it.

### 1. Install

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

### 2. Add an extra directory

In OpenVDM's web interface, add an extra directory for the reports (Configuration > Extra
Directories): name `NAV_QA`, destination e.g. `Products/NAV_QA`. The hook writes each run's
products and reports there, replacing the previous run's. Another name goes in the site TOML's
`[openvdm] output_extra_directory`.

### 3. Write the site TOML

OpenVDM knows the cruise (ID, name, PI, dates) and where each transfer's files go, but not which
of a collection system's files are the GNSS feed, or their format. The **site TOML** says so,
once per ship, with the vessel's details for the reports.

`site-config` writes it by asking for each field, and works offline:

```bash
cd /opt/nav-qa-processing
.venv/bin/nav-qa-processing site-config        # writes configs/site.toml
```

It asks for, in order (Enter keeps the `[default]`, `-` clears it):

1. **The vessel and the report contact:** the vessel's name, the contact institution and
   email, and the vessel's top speed [m s-1] (1 knot is 0.514 m s-1; a faster step counts as
   an unreasonable speed). From OpenVDM 2.17, `openvdm.yaml`'s `vessel` block supplies the
   defaults for the name and contact; what you enter here wins over it.
2. **The primary GNSS feed:**
   - its **format**, from the list of formats with a reader. `nav-qa-processing formats ID`
     describes one, with example lines to compare with your files. OpenRVDAS's
     `POSMV_GGA-*.txt` (an ISO timestamp, a comma, then a GGA) is `nav33`; OpenRVDAS with a
     space before the sentence is `nav30`;
   - a **glob of its files** within the collection system transfer's directory, e.g.
     `raw/POSMV_GGA-*.txt`. Only files matching it are read, and only a transfer that brings
     new or updated matching files runs the QA. Blank means every file;
   - the **receiver or INS** (make, model, serial number, location), for the reports;
   - the **transfer** it's in, e.g. `OpenRVDAS`, when the ship has several collection systems
     with position sensors. Blank: whichever transfer's hook runs.
3. **Other position sensors**, e.g. a second INS: a name for each, then the same questions. Each
   gets its own reports, `<CRUISE>_<name>_...`; the first feed is the primary, whose track sets
   OpenVDM's cruise extent.
4. **The GeoCSV attribution**, e.g. `R/V Roger Revelle, Scripps Institution of Oceanography`,
   and the **extra directory** from step 2.
5. **An expected cruise area**, only used when the hook doesn't set OpenVDM's cruise extent
   itself (see step 4).

The result, for a ship logging a POS MV through OpenRVDAS:

```toml
[vessel]
name = "Robert Gordon Sproul"

[nav]
format = "nav33"
files = "raw/POSMV_GGA-*.txt"

[nav.device]
make = "Applanix"
model = "POS MV"
location = "main lab"

[openvdm]
output_extra_directory = "NAV_QA"

[provenance]
contact_institution = "Scripps Institution of Oceanography"
contact_email = "ship-tech@example.org"

[geocsv]
attribution = "R/V Robert Gordon Sproul, Scripps Institution of Oceanography"

[thresholds]
max_speed = 5.4
```

`configs/site.example.toml` shows every key, commented, including several sensors as
`[[nav]]` tables; the README's [Site TOML](README.md#site-toml) table describes each. Run
`site-config --force` to change the file (its current values become the defaults), or edit it:
the other QA thresholds and the report theme are edited by hand. A misspelled or misplaced key
is an error that says where it belongs.

To check the site TOML against OpenVDM's current cruise without running the QA, print the
config the hook would use:

```bash
.venv/bin/nav-qa-processing config-from-openvdm OpenRVDAS --site-config configs/site.toml -o -
```

It shows the cruise's dates, the feed's format and files, the vessel and the extent, and notes
what's missing or assumed (e.g. an open-ended cruise, which ends today).

### 4. Add the hook to openvdm.yaml

Add a `postCollectionSystemTransfer` entry for the collection system that logs the GNSS to
OpenVDM's `openvdm.yaml` (`/opt/openvdm/server/etc/openvdm.yaml` by default), under
`postHookCommands` (which the file already has):

```yaml
postHookCommands:
    postCollectionSystemTransfer:
        - collectionSystemTransferName: OpenRVDAS
          commandList:
          - name: "Navigation QA"
            command:
            - /opt/nav-qa-processing/.venv/bin/nav-qa-processing
            - openvdm
            - "{collectionSystemTransferName}"
            - "--openvdm-config=/opt/openvdm/server/etc/openvdm.yaml"
            - "--site-config=/opt/nav-qa-processing/configs/site.toml"
            - "--changed-files={newFiles}"
            - "--changed-files={updatedFiles}"
```

| Line | What it is |
|---|---|
| `collectionSystemTransferName: OpenRVDAS` | The collection system transfer's name, exactly as in OpenVDM (Configuration > Collection System Transfers) |
| `/opt/.../nav-qa-processing`, `openvdm` | The install from step 1, and the hook command |
| `"{collectionSystemTransferName}"` | OpenVDM fills in the transfer's name: the hook reads that transfer's directory, and runs the feeds whose `transfer` is it or blank |
| `--openvdm-config` | `openvdm.yaml` itself, for OpenVDM's web address (`siteRoot`), `workerApiKey` and `vessel` block. This path is the default; give yours if OpenVDM is installed elsewhere |
| `--site-config` | The site TOML from step 3, by its full path |
| `--changed-files={newFiles}`, `={updatedFiles}` | The transfer's new and updated files, so the QA runs only when GNSS files changed. Keep the `--changed-files=` form: OpenVDM drops a bare `{newFiles}` argument when there are none |

Optional arguments, one list item each: `--execute-notebook` saves the notebook with its outputs
(an install without `--no-dev`); `--theme=/path/theme.toml` changes the reports' look;
`--no-set-extent` leaves OpenVDM's cruise extent alone (below).

**The cruise extent.** From OpenVDM 2.17 the hook sets the cruise's bounding box
(`cruiseExtent`) to the fixes that pass QC, so a jump to 0, 0 doesn't stretch it. That needs
`workerApiKey` in `openvdm.yaml` to match `WORKER_API_KEY` in OpenVDM's `www/app/Core/Config.php`.
OpenVDM's own tracklines hook (`build_cruise_tracks.py`) sets it too, from unchecked positions:
give that hook `--no-set-extent` so it doesn't overwrite this one. With `--no-set-extent` on this
hook instead, it checks the fixes against OpenVDM's extent, or else the site TOML's `[extent]`.

OpenVDM reads `openvdm.yaml` when its workers start, so restart the hook worker after editing it:

```bash
sudo supervisorctl restart openvdm:post_hooks
```

### 5. Try it

The hook runs after the next transfer that brings new or updated GNSS files. To run it now, give
the same command by hand, as root (as OpenVDM runs it) and without `--changed-files`, which
makes it assess the feed whatever changed:

```bash
sudo /opt/nav-qa-processing/.venv/bin/nav-qa-processing openvdm OpenRVDAS \
    --openvdm-config=/opt/openvdm/server/etc/openvdm.yaml \
    --site-config=/opt/nav-qa-processing/configs/site.toml
```

It prints the cruise and feed it found, the QA summary and the files it wrote into the extra
directory (in the cruise's directory, e.g. `Products/NAV_QA`). OpenVDM's runs are logged in
`/var/log/openvdm/post_hooks.log`, and a failure shows in OpenVDM as the hook's one-line
message.

- **"every nav file is outside the cruise window":** the GNSS files' dates aren't within the
  cruise's start and end dates in OpenVDM. OpenVDM's sample data
  ([openvdm_sample_data](https://github.com/OceanDataTools/openvdm_sample_data)) is from
  2012-04-28 and 29, so a sample cruise needs those dates.
- **"no new or updated nav files; nothing to do":** none of the files given with
  `--changed-files` match the site TOML's `files` glob; by hand, leave `--changed-files` out.
- **"0 fixes ... from 0 files":** the `files` glob matches nothing in the transfer's directory;
  check it with `ls` in that directory.

The README's [OpenVDM section](README.md#openvdm) describes everything the hook does: the
checksums it checks, the MD5 summary it updates, and how several sensors are handled.

## Check the install

```bash
.venv/bin/nav-qa-processing --version
.venv/bin/nav-qa-processing formats              # the format IDs and which can be read
```

To run the QA on a real fileset, unpack one of the committed references (needs `xz`:
`xz-utils` on Debian and Ubuntu, `xz` on RHEL):

```bash
tar -xJf tests/data/BH19-30_136141_gnss.tar.xz -C /tmp
.venv/bin/nav-qa-processing run /tmp/BH19-30_136141_gnss -f nav31 -o /tmp/navqa
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

Installed: [COOKBOOK.md](COOKBOOK.md) has worked examples, on a directory of raw files and on an
R2R fileset.
