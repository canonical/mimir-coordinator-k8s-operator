def get_relation_data(relations, endpoint, key):
    """Retrieve the value for a given key from the local_app_data of a relation with the specified endpoint."""
    return next((r.local_app_data[key] for r in relations if r.endpoint == endpoint), None)
