# COGO Workbench for QGIS

A pure Python plugin inspired by the course-entry workflow of ArcGIS Pro COGO.
Uses QGIS, its bundled Qt, and the Python standard library. No pip packages or
ArcPy. Independent of Esri. Paste and traverse stay on this computer. Reading a
deed is optional: a local Ollama model never leaves the machine, or xAI (Grok)
sends page images to `https://api.x.ai` when you set `XAI_API_KEY` or a QGIS
authentication config. The key is not stored in the plugin.

## Install

1. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Select `dist/cogo_workbench-1.2.1.zip` from this repository.
3. Enable **COGO Workbench**. Open it from the toolbar or **Vector → COGO Workbench**.

Alternatively, copy the `cogo_workbench` directory into the active QGIS profile's
`python/plugins` directory, restart QGIS, and enable it. QGIS **Settings → User
Profiles → Open Active Profile Folder** locates the profile. Targets QGIS 3.40+
and Qt 5/6; integration tests run against the locally installed QGIS 4.2.3.

## Paste a legal description

1. On **Traverse**, choose the working projected CRS and enter or pick the
   **point of beginning (POB)**. Set any scale or direction offset needed.
2. Open **Paste description**, paste one parcel, and click **Extract calls for review**.
3. Review every extracted call alongside its source clause. Highlighted boundary
   clauses block loading; correct the source and extract again, or enter the
   description manually on Traverse. Commencement ties are normally shown but excluded.
   If the text says “Commence” at the parcel's starting corner and all subsequent
   calls form the boundary, check **All calls are boundary (no separate commencement
   tie)** and extract again. A lone closing POB reference is flagged for this explicit
   choice, rather than silently excluding every course. Verify the source before
   selecting this option; genuine commencement ties must stay excluded.
4. Choose the record units. Plain “feet” requires an explicit choice between
   international and US survey feet. The selected unit must match stated units;
   mixed units are rejected. Unstated units inherit a stated description-wide unit
   or another call's unit; if no unit is stated, explicitly choose the intended unit.
5. Check the review box and click **Replace Traverse with reviewed boundary**.
   This replaces the course table, clears closure targets and disables Bowditch.
   It retains your start coordinates, working CRS, scale and direction offset.
   Existing map layers are unaffected. No features are created yet.
6. Use **Preview / closure**, then create lines, a polygon or corner points.

Try the fictitious `examples/legal_rectangle.txt` or `examples/legal_curved_parcel.txt`.
The curved example excludes a 500-foot commencement tie and maps a semicircle
from the POB you supply. Source edits invalidate the extraction and review.

Supported text includes written quadrant bearings (`North 45 degrees 30 minutes
East`), symbol/colon/hyphen DMS, dotted letters (`N. 45° 30' 15" E.`), decimal quadrant bearings, cardinal directions,
decimal distances and comma thousands separators. Separate calls with **thence**,
semicolons, or separate lines that begin with a bearing. Wrapped prose stays
together. Numbered lists and Markdown bold formatting are accepted. Calls can
put “along [named line], a distance of …” between the bearing and distance.
Recognized preambles, course-list headings and acreage annotations appear as context rows.
A caption that only names a section, township or range is context, not a course.
`West along the south line, 208 feet` and `North parallel to the east line 208 feet`
are one course each: the other compass words name the line being followed.
`along the property line 50 feet` is read when it does not also say `distance of`.
Two distances in one clause stay unresolved. A description that says
“For a point of beginning, commence at the corner, thence …” and returns to that
point is the boundary, not a separate commencement tie.

A four-digit fraction such as `North 00.2013 West` is rejected. Write `00:20:13`.
On the course grid, `45.3030` is still decimal degrees.

Curves require an explicit **curve to the right/left**, radius, **arc length**,
**chord length/distance**, or **delta/central angle**, plus a labeled **chord
bearing**, **tangent bearing**, or **the chord of which bears**. A distance after
that chord phrase is the chord length. Chord-only input selects the minor arc. When
multiple curve dimensions are given, their agreement is checked: chord tolerance
is the larger of 0.02 record units or 0.001%; delta tolerance is 1 arc-second.
Conflicting dimensions are flagged. Multiple bearings in one clause are flagged
for manual resolution. Tangent directions are not inferred from preceding calls.

The parser runs entirely offline. It extracts conventional explicit calls, not
every form of legal prose. Missing directions, exceptions, approximate distances,
unsupported units and unmatched clauses remain visible for resolution. It does
not look up monuments, geocode a parcel, or choose survey control. For a
commencement description, the coordinates you supply must be the POB; the plugin
does not compute it from the excluded ties. Paste one description at a time
(100,000 characters and 2,000 clauses maximum).

Wording such as “along the arc of a curve to the right, concave westerly” is a
normal curve when the turn, radius, arc or chord or delta, and a labeled chord
or tangent bearing are all written down. Chord-only input is still the minor arc.
Tangent directions are still not inferred from the previous course.

## Read a deed

On **Paste description**, choose **Read a deed or document**. Pick a PDF, PNG,
JPG or TIF. Every page is rendered and sent as an image; text embedded in a PDF
is not used. **Ollama (local)** is the default (`ollama pull deepseek-ocr`, with
`qwen2.5vl` or `gemma4` as fallbacks). **xAI (Grok)** uses model `grok-4.7` at
`https://api.x.ai/v1`. Set `XAI_API_KEY` in the environment, or select a QGIS
Basic authentication config whose password is the key. A custom
OpenAI-compatible server is the third choice; use HTTPS unless it is on this
computer.

The description ends at the first “containing … acres” line, so a second lot stays out.
The offline parser reads the description first. **Ask AI about unread calls**
sends only the clauses the parser could not read, and only after you have a
model selected. The model must quote the deed. A call is dropped when the quote
is not in the text, a number in the call is not in the quote, or the bearing, turn,
and chord or tangent are not in that quote. A quote with a second “thence” is dropped.
Review every call, choose the record units, and **Load courses into Traverse**.
Feet in the text still need international or US survey feet. A stated meter call
will not load as feet. Then set the point of beginning, **Preview / closure**, and
**Create corner points**. No layer is created by the reader. Page images are sent
at the rendered size up to 4500 pixels. Hosted models can charge for the request.

## Traverse from a legal description

1. Choose a **projected working CRS** appropriate for the property. Enter the
   starting easting/northing, or **Pick start** on the map. Picking honors current
   QGIS snapping settings and transforms the picked location to the working CRS.
2. Choose the record's units: CRS units, meters, international feet, or US survey
   feet. Set a grid/ground scale and direction offset if needed. Default scale is
   1 and offset is 0. These are manual corrections, not automatic survey calibration.
3. Add straight or curved courses in order. Use a quadrant bearing such as
   `N45:30:00E`, `S12°15'30"W`, or a clockwise-from-north decimal azimuth such as
   `135.5`. On the course grid, `45.3030` is decimal degrees, never packed degrees/minutes/seconds.
   Due east, north, south and west display as E, N, S and W. The course table is saved in the QGIS project.
4. For a curve, specify a positive radius, R/L turn, tangent or chord bearing,
   and a value interpreted as **Arc** length, **Chord** length, or **Delta** angle.
   Delta accepts decimal degrees or separated DMS. Chord input uses the minor arc;
   use Arc or Delta to describe a major arc. Each course needs its own direction.
5. **Preview / closure** displays the traverse and reports total grid length,
   easting/northing error, endpoint, misclosure and relative closure. A blank
   closure target means the start point. Set a known endpoint for an open traverse.
6. Optionally apply **Bowditch adjustment** to straight courses. Error is
   distributed by course length. Curved traverses cannot be Bowditch adjusted.
7. Create **COGO lines**, a **polygon**, and/or **corner points**. Each action
   creates a new temporary layer. Export it to GeoPackage to preserve the result.

Lines and polygons retain true circular geometry. A polygon must return to its start within
the entered tolerance (in CRS units). A gap that is only rounding is snapped onto the
start. A larger gap inside that tolerance becomes a closing straight connector and is
recorded in `closure_gap`. Crossing and zero-area polygons are rejected. For an open traverse adjusted to other control, polygon
creation still requires closure back to the starting point.

Import/export course tables using the CSV schema in `examples/`. Import validates
all rows before replacing the table. CSV contains **courses only**: retain the
CRS, coordinates, units, scale and offset separately. The rectangle example is
100 × 200 record units. The curved example is a semicircle of radius 100 and its
200-unit closing chord. Examples are fictitious.

## Update COGO lines

Activate a line layer, select the courses, and open **Update / Inverse**. Match
the working CRS to the layer CRS and use the correct record units/scale/offset.
**Update selected COGO lines** asks before it writes, then calculates attributes from current geometry. It
does not rebuild geometry from attributes. The optional NULL-only mode preserves
existing values. Clear NULL-only mode to consistently recalculate all values.

Updates support two-vertex straight lines and true three-point circular arcs,
including a single curve inside a CompoundCurve. Split multipart or multi-course
features first using QGIS editing/processing tools. Densified polylines are not
silently fitted to circles. Every selected feature and existing field type is
validated before editing begins. Missing measurement fields are added in the
edit buffer. Each update is one undoable edit command; unrelated edits remain
intact. Review, then explicitly **Save Layer Edits**, **Undo**, or roll back in QGIS.

Inverse calculates record bearing/distance between two entered coordinates with
the same conversion settings.

## Attribute conventions

| Field | Meaning |
| --- | --- |
| `direction` | Record azimuth in decimal degrees, clockwise from north; chord azimuth on a curve |
| `bearing` | Formatted quadrant bearing |
| `distance` | Straight length or curve chord length in record units |
| `radius`, `arclength` | Positive curve radius and arc length in record units; NULL for straight lines |
| `delta`, `side` | Curve central angle in degrees and L/R; NULL for straight lines |
| `course` | One-based input order |
| `record_dir`, `record_val`, `record_rad`, `record_mode` | Original entered call, retained even after adjustment |
| `adjusted` | 1 when output geometry has been Bowditch adjusted |
| `cogo_unit` | Chosen record unit label |
| `grid_factor` | Record-length-to-CRS conversion including scale |
| `dir_offset` | Degrees added to record azimuth to obtain grid azimuth |
| `area_grid` | Polygon area in square CRS units |
| `closure_gap` | Polygon's closing connector length in CRS units |

Grid length = record length × unit conversion × scale.
Grid azimuth = record azimuth + direction offset.
Update COGO and Inverse reverse these conversions. Geometry and all COGO values
are horizontal planar measurements; Z/M are not used. These conventions are
documented for this plugin and are not an Esri geodatabase schema.

## Scope and limits

No unrestricted deed interpretation, spirals, automatic datum/convergence/ground
corrections, parcel fabric, automatic topology adjudication, or external
dependencies. Deed reading is optional and still requires you to check every
call. Record calls, control, unit conversions, curve choice and closure
must be independently verified. This tool does not establish a legal boundary
or substitute for a licensed survey.

## Checks

Rebuild the installable ZIP with `python build_plugin.py` (standard library only).

Pure math and parser tests (any Python 3.9+):

```powershell
python -m unittest discover -s tests -p test_engine.py -v
python -m unittest discover -s tests -p test_legal.py -v
python -m unittest discover -s tests -p test_deed_ai.py -v
python -m unittest discover -s tests -p test_shapes.py -v
```

QGIS integration and math tests on Windows (adjust installation path):

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:\Program Files\QGIS 4.2.3\bin\python-qgis.bat' -m unittest discover -s tests -v
```

Tests cover direction formats, invalid input, ground/grid conversion, left/right
and major curves, closure, Bowditch, actual QGIS curved layers, NULL-only updates,
undo preserving existing edits, existing field names/types, validation before
mutation, plugin lifecycle, GeoPackage curve preservation, dock creation,
preview, true curve polygon areas and corner output. Parser checks cover common
direction formats, commencement exclusion, curves with redundant dimensions,
ambiguous/unsupported calls and units. UI checks verify review requirements,
stale-source invalidation, rejected loads preserving the table, and creation of
a correctly sized polygon from extracted boundary calls.

Workflow references: [ArcGIS Pro Traverse](https://pro.arcgis.com/en/pro-app/3.5/help/editing/create-a-traverse.htm),
[Update COGO](https://pro.arcgis.com/en/pro-app/3.0/tool-reference/editing/update-cogo.htm),
[QGIS circular geometry API](https://api.qgis.org/api/3.44/classQgsCircularString.html).

## License

GNU General Public License v2.0 or later. See `LICENSE`.
