"""Plants and operations from a label, including a label inside a bulk part."""

import io
import zipfile

from rxradar.ingest.spl_sites import label_xmls, parse_sites

LABEL = b"""<document xmlns="urn:hl7-org:v3">
<setId root="abc"/><versionNumber value="7"/><effectiveTime value="20260102"/>
<author><assignedEntity><representedOrganization>
<id extension="111111111" root="1.3.6.1.4.1.519.1"/><name>Label Co</name>
<assignedEntity><assignedOrganization>
 <assignedEntity><assignedOrganization>
  <id extension="222222222" root="1.3.6.1.4.1.519.1"/>
  <id extension="1025114" root="2.16.840.1.113883.4.82"/><name>Plant A</name>
  <addr><country>USA</country></addr>
 </assignedOrganization>
 <performance><actDefinition><code code="C1" displayName="MANUFACTURE"/>
  <product><manufacturedProduct><manufacturedMaterialKind><code code="0338-1"/>
  </manufacturedMaterialKind></manufacturedProduct></product>
  <product><manufacturedProduct><manufacturedMaterialKind><code code="0338-2"/>
  </manufacturedMaterialKind></manufacturedProduct></product>
 </actDefinition></performance>
 <performance><actDefinition><code code="C2" displayName="LABEL"/></actDefinition></performance>
 </assignedEntity>
</assignedOrganization></assignedEntity>
</representedOrganization></assignedEntity></author>
</document>"""


def test_each_plant_operation_and_ndc_is_a_row():
    rows = parse_sites(LABEL)
    assert [(r["operation"], r["ndc"]) for r in rows] == [
        ("MANUFACTURE", "0338-1"),
        ("MANUFACTURE", "0338-2"),
        ("LABEL", None),
    ]
    first = rows[0]
    assert first["set_id"] == "abc" and first["version"] == 7
    assert first["labeler_duns"] == "111111111" and first["plant_duns"] == "222222222"
    assert first["plant_fei"] == "1025114" and first["plant_name"] == "Plant A"
    assert first["country"] == "USA"


def test_a_label_without_plants_gives_no_rows():
    assert parse_sites(b'<document xmlns="urn:hl7-org:v3"><setId root="x"/></document>') == []


def test_labels_are_read_from_zips_inside_a_part(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("label.xml", LABEL)
        z.writestr("image.jpg", b"x")
    part = tmp_path / "part.zip"
    with zipfile.ZipFile(part, "w") as z:
        z.writestr("prescription/one.zip", inner.getvalue())
    assert list(label_xmls(part)) == [LABEL]
