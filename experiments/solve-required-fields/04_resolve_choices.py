#!/usr/bin/env python3
"""Step 4: the error names the fields and their ids. Is that enough to ACT?

For a free-text or integer field, yes. For a tagger (a fixed choice list) the
error says the field is required and gives its type - but not its allowed values.
This measures what a second lookup costs, using only the ids the error handed us.
"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
from zd import call

# ids taken verbatim from details.base[].ticket_field_id in the 422
FROM_ERROR = [(360028956373, "FieldAssignee"),
              (29450755118359, "FieldInteger"),
              (42288679067287, "FieldTagger")]

for fid, ftype in FROM_ERROR:
    st, _, doc, raw = call("GET", f"/api/v2/ticket_fields/{fid}.json")
    f = (doc or {}).get("ticket_field", {})
    opts = f.get("custom_field_options") or []
    print(f"  {ftype:<14} id={fid:<16} HTTP {st}  {f.get('title','?').strip()!r}")
    print(f"      type={f.get('type')}  required={f.get('required')}  "
          f"choices={len(opts) if opts else '- (not a choice list)'}")
    for o in opts:
        print(f"        {o['value']!r:<28} {o['name']}")
