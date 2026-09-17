# vulture whitelist: names flagged as unused by vulture but intentionally kept.
# pytest injects fixtures into tests by parameter name, which vulture cannot see.
clear_config_env  # pytest fixture (tests/unit/test_config.py)
env_config  # pytest fixture (tests/unit/test_config.py)
xdg_config  # pytest fixture (tests/unit/test_config.py)
home_config  # pytest fixture (tests/unit/test_config.py)
init  # pytest fixture (tests/unit/test_server.py)
