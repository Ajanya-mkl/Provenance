import pytest

from tests.unit.agent_fixtures import build_warehouse


@pytest.fixture
def warehouse(tmp_path):
    return build_warehouse(tmp_path / "warehouse.duckdb")
