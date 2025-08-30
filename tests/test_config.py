import yaml, pathlib
def test_values_yaml_is_valid():
    path = pathlib.Path('helm/lse-stack/values.yaml')
    data = yaml.safe_load(path.read_text())
    assert 'appConfig' in data
    assert data['appConfig']['tickers'], 'Tickers list should not be empty'
