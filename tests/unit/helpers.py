import yaml

def get_relation_data(relations, endpoint, key):
    """Retrieve the value for a given key from the local_app_data of a relation with the specified endpoint."""
    relevant = [r.local_app_data[key] for r in relations if r.endpoint == endpoint]
    assert len(relevant) < 2, "This helper currently assumes only one relation."
    return relevant[0] if relevant else None

def get_worker_config_exemplars(relations, endpoint):
    # Find the relevant relation
    relevant = [r.local_app_data for r in relations if r.endpoint == endpoint]
    
    # Ensure that we found the relevant relation
    assert relevant, "No matching relation found"

    # Get the worker config string from the relevant data
    worker_config_str = relevant[0]['worker_config']

    # Parse the YAML string into a Python dictionary
    worker_config = yaml.safe_load(worker_config_str)
    worker_config = yaml.safe_load(worker_config)

    # Assert the types of the parsed object
    assert isinstance(worker_config, dict), f"Expected worker_config to be a dictionary, got {type(worker_config)}"
    assert 'limits' in worker_config, "Missing 'limits' in worker_config"
    assert isinstance(worker_config['limits'], dict), f"Expected 'limits' to be a dictionary, got {type(worker_config['limits'])}"

    # Return the 'max_global_exemplars_per_user key' of the 'limits' section of the worker config
    return worker_config['limits']['max_global_exemplars_per_user']