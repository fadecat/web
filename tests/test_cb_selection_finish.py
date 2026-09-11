from backend.services.cb_template_migration import migrate_config_to_v3


def test_catalog_http_has_typed_fields_and_business_states(contract_client):
    response = contract_client.get('/api/cb-list/factors/catalog')
    assert response.status_code == 200
    catalog = {x['field']: x for x in response.json()}
    assert 'redeem_icons' not in catalog
    assert catalog['price']['filterable'] is True
    assert 'NEAR_MATURITY' in {x['value'] for x in catalog['redeem_status_code']['options']}


def test_invalid_business_enum_and_duplicate_condition_ids_rejected_before_fetch(contract_client):
    base = {'schema_version': 3, 'id': 't', 'name': 't', 'source': 'live', 'conditions': [{'id': 'r', 'field': 'redeem_status_code', 'op': 'not_in', 'value': ['NOT_A_STATUS']}]}
    assert contract_client.post('/api/cb-list/screen', json=base).status_code == 422
    base['conditions'] = [{'id': 'r', 'field': 'price', 'op': 'gte', 'value': 100}]*2
    assert contract_client.post('/api/cb-list/screen', json=base).status_code == 422


def test_legacy_icons_direct_run_is_blocked(contract_client):
    body={'schema_version':3,'id':'t','name':'t','conditions':[{'id':'r','field':'redeem_icons','op':'not_any','value':['R']}]}
    assert contract_client.post('/api/cb-list/screen',json=body).status_code == 409
