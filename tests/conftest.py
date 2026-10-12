import pytest

from core.eta import BaselineEta
from core.generator import generate_history, load_params, load_scenarios


@pytest.fixture(scope="session")
def params():
    return load_params()


@pytest.fixture(scope="session")
def scenarios():
    return load_scenarios()


@pytest.fixture(scope="session")
def model(params):
    # 테스트는 빠른 기준모형으로 돌림(GBM 학습 생략)
    hist = generate_history(params)
    return BaselineEta(params["history"]["min_group_n"], params["history"]["min_carrier_n"]).fit(
        hist, len(params["arrival"]["checkpoints"]))
