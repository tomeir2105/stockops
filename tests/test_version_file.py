def test_version_file_present():
    v = open('VERSION').read().strip()
    parts = v.split('.')
    assert len(parts) == 3 and all(x.isdigit() for x in parts)
