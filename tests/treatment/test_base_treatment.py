import pytest
import pandas as pd

from vivarium.framework.components import ComponentConfigError
from vivarium.test_util import metadata

from ceam_public_health.treatment import Treatment


@pytest.fixture(scope='function')
def config(base_config):
    base_config.update({
        'test_treatment': {
            'dose_response': {
                'onset_delay': 14,  # Days
                'duration': 720,  # Days
                'waning_rate': 0.038  # Percent/Day
            },
        }
    }, **metadata(__file__))
    return base_config


@pytest.fixture(scope='function')
def builder(mocker, config):
    builder = mocker.MagicMock()
    builder.configuration = config
    return builder


@pytest.fixture(scope='function')
def treatment(builder):
    tx = Treatment('test_treatment', 'test_cause')

    protection = {'first': 0.5, 'second': 0.7}
    tx.get_protection = lambda builder_: protection

    tx.setup(builder)

    tx.clock = lambda: pd.Timestamp('07-02-2005')


def test_setup(builder):
    tx = Treatment('not_a_treatment', 'test_cause')

    with pytest.raises(ComponentConfigError):
        tx.setup(builder)

    tx = Treatment('test_treatment', 'test_cause')

    with pytest.raises(NotImplementedError):
        tx.setup(builder)


def test_get_protection(builder):
    tx = Treatment('test_treatment', 'test_cause')

    with pytest.raises(NotImplementedError):
        tx._get_protection(builder)

    with pytest.raises(NotImplementedError):
        tx.get_protection(builder)

    protection = {'first': 0.5, 'second': 0.7}
    tx.get_protection = lambda builder_: protection

    assert tx._get_protection(builder) == protection

