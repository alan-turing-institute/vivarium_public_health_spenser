"""
==========================
Vivarium SPENSER testing Utilities
==========================

Utility functions and classes to make testing ``vivarium`` components easier.

"""
from pathlib import Path

import numpy as np
import pandas as pd

from vivarium.framework import randomness


class NonCRNTestPopulation:

    configuration_defaults = {
        'population': {
            'age_start': 0,
            'age_end': 100,
            'exit_age': None,
        },
    }

    @property
    def name(self):
        return "non_crn_test_population"

    def setup(self, builder):
        self.config = builder.configuration
        self.randomness = builder.randomness.get_stream('population_age_fuzz', for_initialization=True)
        #columns = ['age', 'sex', 'location', 'alive', 'entrance_time', 'exit_time']
        columns =  ['age','sex','location','ethnicity','alive','entrance_time', 'exit_time']
        self.population_view = builder.population.get_view(columns)

        builder.population.initializes_simulants(self.generate_test_population,
                                                 creates_columns=columns)

        builder.event.register_listener('time_step', self.age_simulants)

    def generate_test_population(self, pop_data):

        age_start = pop_data.user_data.get('age_start', self.config.population.age_start)
        age_end = pop_data.user_data.get('age_end', self.config.population.age_end)
        location = self.config.input_data.location

        population = _non_crn_build_population(pop_data.index, age_start, age_end, location,
                                               pop_data.creation_time, pop_data.creation_window, self.randomness)
        self.population_view.update(population)

    def age_simulants(self, event):
        population = self.population_view.get(event.index, query="alive == 'alive'")
        population['age'] += event.step_size / pd.Timedelta(days=365)
        self.population_view.update(population)


class TestPopulation(NonCRNTestPopulation):

    @property
    def name(self):
        return "test_population"

    def setup(self, builder):
        super().setup(builder)
        self.age_randomness = builder.randomness.get_stream('age_initialization', for_initialization=True)
        self.register = builder.randomness.register_simulants

    def generate_test_population(self, pop_data):

        # this part is then rewriten by the _build_population and the SPENSER data but I leave it as it is for now.
        age_start = pop_data.user_data.get('age_start', self.config.population.age_start)
        age_end = pop_data.user_data.get('age_end', self.config.population.age_end)
        age_draw = self.age_randomness.get_draw(pop_data.index)
        if age_start == age_end:
            age = age_draw * (pop_data.creation_window / pd.Timedelta(days=365)) + age_start
        else:
            age = age_draw * (age_end - age_start) + age_start

        core_population = pd.DataFrame({'entrance_time': pop_data.creation_time,'age': age.values}, index=pop_data.index)
        self.register(core_population)
        #
        population = _build_population(core_population,self.config.path_to_pop_file)
        self.population_view.update(population)


def _build_population(core_population, path_to_data_file):

    index = core_population.index
    core_population_ = pd.read_csv(path_to_data_file)

    population = pd.DataFrame(
        {'age': core_population_['age'].astype(float),
         'entrance_time': core_population['entrance_time'],
         'sex': core_population_['sex'],
         'alive': pd.Series('alive', index=index),
         'location': core_population_['location'],
         'ethnicity': core_population_['ethnicity'],
         'exit_time': pd.NaT, },
        index=index)
    return population


def _non_crn_build_population(index, age_start, age_end, location, creation_time, creation_window, randomness_stream):
    if age_start == age_end:
        age = randomness_stream.get_draw(index) * (creation_window / pd.Timedelta(days=365)) + age_start
    else:
        age = randomness_stream.get_draw(index)*(age_end - age_start) + age_start

    population = pd.DataFrame(
        {'age': age,
         'sex': randomness_stream.choice(index, ['Male', 'Female'], additional_key='sex_choice'),
         'alive': pd.Series('alive', index=index),
         'location': location,
         'entrance_time': creation_time,
         'exit_time': pd.NaT, },
        index=index)
    return population


def build_table(value, year_start, year_end, columns=('age', 'year', 'sex', 'value')):
    value_columns = columns[3:]
    if not isinstance(value, list):
        value = [value]*len(value_columns)

    if len(value) != len(value_columns):
        raise ValueError('Number of values must match number of value columns')

    rows = []
    for age in range(0, 140):
        for year in range(year_start, year_end+1):
            for sex in [1,2]:
                r_values = []
                for v in value:
                    if v is None:
                        r_values.append(np.random.random())
                    elif callable(v):
                        r_values.append(v(age, sex, year))
                    else:
                        r_values.append(v)
                rows.append([age, age+1, year, year+1, sex] + r_values)
    return pd.DataFrame(rows, columns=['age_start', 'age_end',
                                       'year_start', 'year_end', 'sex']
                                      + list(value_columns))


def make_dummy_column(name, initial_value):
    class DummyColumnMaker:

        @property
        def name(self):
            return "dummy_column_maker"

        def setup(self, builder):
            self.population_view = builder.population.get_view([name])
            builder.population.initializes_simulants(self.make_column,
                                                     creates_columns=[name])

        def make_column(self, pop_data):
            self.population_view.update(pd.Series(initial_value, index=pop_data.index, name=name))

        def __repr__(self):
            return f"dummy_column(name={name}, initial_value={initial_value})"
    return DummyColumnMaker()


def get_randomness(key='test', clock=lambda: pd.Timestamp(1990, 7, 2), seed=12345, for_initialization=False):
    return randomness.RandomnessStream(key, clock, seed=seed, for_initialization=for_initialization)


def reset_mocks(mocks):
    for mock in mocks:
        mock.reset_mock()


def metadata(file_path):
    return {'layer': 'override', 'source': str(Path(file_path).resolve())}

def build_mortality_table(input_df, year_start, year_end, age_start,age_end):

    '''Make a mortality table based on the input data'''

    df = pd.read_csv(input_df)

    unique_locations = np.unique(df['location'])
    unique_sex = np.unique(df['sex'])
    unique_ethnicity = np.unique(df['ethnicity'])

    list_dic = []
    for loc in unique_locations:
        for eth in unique_ethnicity:
            for age in range(age_start,age_end):
                for sex in unique_sex:

                    mean_value = abs(1- (age_end- age))/1000
                    value = np.random.normal(mean_value,mean_value*0.05)

                    # do some extreme cases for testing
                    if eth==2:
                        value = 0
                    if loc=='E02002183':
                        value = 1

                    dict= {'location':loc,'ethnicity':eth,'age_start':age,'age_end':age+1,'sex':sex,'year_start':year_start,'year_end':year_end, 'mean_value':value}
                    list_dic.append(dict)


    return pd.DataFrame(list_dic)

