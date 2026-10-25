# Raw navigation format definitions

One TOML file per R2R raw navigation format ID (NavManager's `nav1` … `nav33`, `uhdas`,
`siomet`, `metoc_nav`), plus `sentences.toml` for the NMEA and proprietary sentences they use.
IDs pad the number to two digits so they sort in order (`nav01` … `nav33`); each file is named
after its `id`. NavManager's one-digit spelling (`nav2`) is still read, as the same ID
(`catalog.canonical_id`), in configs, site TOMLs and `-f`.
`formats/catalog.py` loads them, and `nav-qa-processing formats [ID]` prints them.

The facts (record layouts, sentences, vessels) follow NavManager's `doc/fileformat/*.txt` and
the comments in its `navcopy`/`navdatalist`, but the text is written for this project, and it
corrects errors in the originals. A format with a reader adds what reading real filesets showed.

Keys, in this order (`tests/test_formats.py` checks them):

| Key | What it holds |
|---|---|
| `id` | The format ID, as the file's name |
| `title` | `<system>: <what a line holds>`. A year after the system only to tell its layouts apart (`WHOI Calliope (2009)`); no vessel names |
| `system` | The logger or acquisition system that wrote the files |
| `file_type` | Text encoding, delimiters, one sentence per line or several |
| `vessels` | Ships known to have used it |
| `sentences` | The NMEA and proprietary sentences in the files (each defined in `sentences.toml`) |
| `r2r_formats` | R2R's format IDs for this layout; see `notes` when the match is by layout only |
| `position`, `time` | Where the fix and its date and time come from |
| `description` | What a line holds, where position and time come from, and when and where the format was used |
| `notes` | Quirks of the format and its files, and how R2R files them |
| `reader` | For a format with a reader: how this package reads it (how a fix is dated, what's skipped and reported) and where NavManager differs, citing the rows of `docs/navmanager-differences.md` (R5, Q7, …) |
| `filesets` | For a format with a reader: the reference filesets that test it, with what each shows; those committed in `tests/data/` say so |
| `references` | Specifications |
| `examples_from` | Where the examples come from: a fileset and file, or NavManager's document |
| `examples` | Lines of a real file, consecutive, with tabs as `\t` |
| `[[fields]]` | `name`, `example`, `meaning` for each column of a line |

Only nav07 (never implemented) lacks a layout, and nav07 and nav23 (no signed sample found)
lack examples. Prefer examples from released filesets to NavManager's samples.
