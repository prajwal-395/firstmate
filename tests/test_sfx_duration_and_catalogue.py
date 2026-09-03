import pytest
from library.tools.sfx_library import catalog_document, load_sfx_catalog
from library.tools.sfx_duration import resolve_played_seconds

def test_the_catalogue_advertised_duration_is_accepted_by_the_contract(tmp_path):
    """Proves a model answer that copies the catalogue's advertised duration is ACCEPTED
    on the two named files that caused the failure."""
    
    # 1. camera soft click.wav
    # Advertised: 0.46 s, measured length: 0.4591609977324263
    
    # 3. edge case 0.4599999
    # Formatting to .5f makes it 0.46000 and incorrectly slices as 0.46
    
    catalog_data = [
        {
            "sfx_id": "camera soft click.wav",
            "duration_seconds": 0.4591609977324263,
            "category": "Foley"
        },
        {
            "sfx_id": "camera-shutter-6305.mp3",
            "duration_seconds": 0.3395918367346939,
            "category": "Foley"
        },
        {
            "sfx_id": "edge-case.wav",
            "duration_seconds": 0.4599999,
            "category": "Foley"
        }
    ]
    
    import json
    index_file = tmp_path / "sfx_index.json"
    index_file.write_text(json.dumps(catalog_data))
    
    # Actually load_sfx_catalog expects the directory
    # but we can just use the entries from our data to simulate the catalog rows.
    # Wait, catalog_document calls catalog_rows internally.
    doc = catalog_document(catalog_data)
    
    # The document should have the truncated values.
    # camera soft click.wav should advertise "plays for 0.45 s"
    # camera-shutter-6305.mp3 should advertise "plays for 0.33 s"
    
    import re
    # Find the duration in the doc for the first file
    match_1 = re.search(r'category Foley \| plays for ([\d\.]+) s', doc)
    assert match_1, f"Could not find identity line in:\n{doc}"
    advertised_1 = float(match_1.group(1))
    
    # Test it against the contract (resolve_played_seconds)
    # This must NOT raise SfxDurationRefused
    played_1 = resolve_played_seconds(catalog_data[0], 0.0, advertised_1, 30.0)
    assert played_1 == advertised_1
    
    match_2 = re.search(r'camera-shutter-6305.*?category Foley \| plays for ([\d\.]+) s', doc, re.DOTALL)
    assert match_2, f"Could not find identity line in:\n{doc}"
    advertised_2 = float(match_2.group(1))
    
    played_2 = resolve_played_seconds(catalog_data[1], 0.0, advertised_2, 30.0)
    assert played_2 == advertised_2
    
    match_3 = re.search(r'edge-case\.wav.*?category Foley \| plays for ([\d\.]+) s', doc, re.DOTALL)
    assert match_3, f"Could not find identity line in:\n{doc}"
    advertised_3 = float(match_3.group(1))
    
    played_3 = resolve_played_seconds(catalog_data[2], 0.0, advertised_3, 30.0)
    assert played_3 == advertised_3
