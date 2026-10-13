# Raw navigation format definitions

One TOML file per R2R raw navigation format ID (NavManager's `nav1` … `nav33`, `uhdas`,
`siomet`, `metoc_nav`), plus `sentences.toml` for the NMEA and proprietary sentences they use.
`formats/catalog.py` loads them, and `nav-qa-processing formats [ID]` prints them.

The facts (record layouts, sentences, vessels) follow NavManager's `doc/fileformat/*.txt` and
the comments in its `navcopy`/`navdatalist`, but the text is written for this project, and it
corrects errors in the originals. The example records are sample data lines from those
documents and from released R2R filesets.

Keys: `id`, `title`, `system`, `file_type`, `vessels`, `sentences`, `position` (where the fix
comes from), `time` (where the date and time come from), `description`, `notes`,
`references`, `examples`, and `[[fields]]` tables (`name`, `example`, `meaning`) for the
columns of a record.
