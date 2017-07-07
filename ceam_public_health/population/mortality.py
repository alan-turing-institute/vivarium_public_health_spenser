from datetime import timedelta

import numpy as np
import pandas as pd

from ceam_inputs import get_life_table, get_cause_deleted_mortality_rate

from ceam import config
from ceam.framework.event import listens_for
from ceam.framework.population import uses_columns
from ceam.framework.util import rate_to_probability
from ceam.framework.values import list_combiner, produces_value, modifies_value


class Mortality:
    def setup(self, builder):
        self._mortality_rate_builder = lambda: builder.lookup(self.load_all_cause_mortality())
        self.mortality_rate = builder.rate('mortality_rate')
        self.death_emitter = builder.emitter('deaths')
        self.life_table = builder.lookup(get_life_table(), key_columns=(), parameter_columns=('age',))
        self.random = builder.randomness('mortality_handler')
        self.csmr_data = builder.value('csmr_data', list_combiner)
        self.csmr_data.source = list
        self.clock = builder.clock()

    @listens_for('post_setup')
    def post_step(self, event):
        # This is being loaded after the main setup phase because it needs to happen after all disease models
        # have completed their setup phase which isn't guaranteed (or even likely) during this component's
        # normal setup.
        self.mortality_rate_lookup = self._mortality_rate_builder()

    def load_all_cause_mortality(self):
        return get_cause_deleted_mortality_rate(self.csmr_data())

    @listens_for('initialize_simulants')
    @uses_columns(['cause_of_death'])
    def load_population_columns(self, event):
        event.population_view.update(pd.Series('not_dead', name='cause_of_death', index=event.index))

    @listens_for('time_step', priority=0)
    @uses_columns(['alive', 'exit_time', 'cause_of_death'], "alive == 'alive'")
    def mortality_handler(self, event):
        prob_df = rate_to_probability(self.mortality_rate(event.index))
        prob_df['no_death'] = 1-prob_df.sum(axis=1)
        prob_df['cause_of_death'] = self.random.choice(prob_df.index, prob_df.columns, prob_df)
        dead_pop = prob_df.query('cause_of_death != "no_death"').copy()

        dead_pop['alive'] = 'dead'
        dead_pop['exit_time'] = event.time

        self.death_emitter(event.split(dead_pop.index))

        event.population_view.update(dead_pop[['alive', 'exit_time', 'cause_of_death']])

    @listens_for('time_step__cleanup')
    @uses_columns(['alive', 'exit_time', 'cause_of_death'])
    def untracked_handler(self, event):
        pop = event.population
        new_untracked = (pop.alive == 'untracked') & (pop.exit_time == pd.NaT)
        pop.loc[new_untracked, 'exit_time'] = event.time
        pop.loc[new_untracked, 'cause_of_death'] = 'untracked'
        event.population_view.update(pop)

    @produces_value('mortality_rate')
    def mortality_rate_source(self, population):
        return pd.DataFrame({'death_due_to_other_causes': self.mortality_rate_lookup(population)})

    @modifies_value('metrics')
    @uses_columns(['alive', 'age', 'cause_of_death'])
    def metrics(self, index, metrics, population_view):
        population = population_view.get(index)
        the_living = population[population.alive == 'alive']
        the_dead = population[population.alive == 'dead']
        the_untracked = population[population.alive == 'untracked']

        metrics['deaths'] = len(the_dead)
        metrics['years_of_life_lost'] = self.life_table(the_dead.index).sum()
        metrics['total_population'] = len(population)
        metrics['total_population__living'] = len(the_living)
        metrics['total_population__dead'] = len(the_dead)
        metrics['total_population__untracked'] = len(the_untracked)

        for (condition, count) in pd.value_counts(the_dead.cause_of_death).to_dict().items():
            metrics['{}'.format(condition)] = count

        return metrics

    @modifies_value('epidemiological_span_measures')
    @uses_columns(['age', 'exit_time', 'cause_of_death', 'alive', 'sex'])
    def calculate_mortality_measure(self, index, age_groups, sexes, all_locations, duration, cube, population_view):
        root_location = config.simulation_parameters.location_id
        pop = population_view.get(index)

        if all_locations:
            locations = set(pop.location) | {-1}
        else:
            locations = {-1}

        now = self.clock()
        window_start = now - duration

        causes_of_death = set(pop.cause_of_death.unique()) - {'not_dead'}

        for low, high in age_groups:
            for sex in sexes:
                for location in locations:
                    sub_pop = pop.query('age >= @low and age < @high and sex == @sex and (alive == "alive" or exit_time > @window_start)')
                    if location >= 0:
                        sub_pop = sub_pop.query('location == @location')

                    if not sub_pop.empty:
                        birthday = sub_pop.exit_time.fillna(now) - pd.to_timedelta(sub_pop.age, 'Y')
                        time_before_birth = np.maximum(np.timedelta64(0), birthday - window_start).dt.total_seconds().sum()
                        time_after_death = np.minimum(np.maximum(np.timedelta64(0), now - sub_pop.exit_time.dropna()), np.timedelta64(duration)).dt.total_seconds().sum()
                        time_in_sim = duration.total_seconds() * len(pop) - (time_before_birth + time_after_death)
                        time_in_sim = time_in_sim/(timedelta(days=364).total_seconds())
                        for cause in causes_of_death:
                            deaths_in_period = (sub_pop.cause_of_death == cause).sum()

                            cube = cube.append(pd.DataFrame({'measure': 'mortality', 'age_low': low, 'age_high': high, 'sex': sex, 'location': location if location >= 0 else root_location, 'cause': cause, 'value': deaths_in_period/time_in_sim, 'sample_size': len(sub_pop)}, index=[0]).set_index(['measure', 'age_low', 'age_high', 'sex', 'location', 'cause']))
                        deaths_in_period = len(sub_pop.query('alive != "alive"'))
                        cube = cube.append(pd.DataFrame({'measure': 'mortality', 'age_low': low, 'age_high': high, 'sex': sex, 'location': location if location >= 0 else root_location, 'cause': 'all', 'value': deaths_in_period/time_in_sim, 'sample_size': len(sub_pop)}, index=[0]).set_index(['measure', 'age_low', 'age_high', 'sex', 'location', 'cause']))
        return cube

    @modifies_value('epidemiological_span_measures')
    @uses_columns(['exit_time', 'sex', 'age', 'location'], 'alive != "alive"')
    def deaths(self, index, age_groups, sexes, all_locations, duration, cube, population_view):
        root_location = config.simulation_parameters.location_id
        pop = population_view.get(index)

        if all_locations:
            locations = set(pop.location) | {-1}
        else:
            locations = {-1}

        now = self.clock()
        window_start = now - duration
        for low, high in age_groups:
            for sex in sexes:
                for location in locations:
                    sub_pop = pop.query('age > @low and age <= @high and sex == @sex')
                    sample_size = len(sub_pop)
                    sub_pop = sub_pop.query('exit_time > @window_start and exit_time <= @now')
                    if location >= 0:
                        sub_pop = sub_pop.query('location == @location')

                    cube = cube.append(pd.DataFrame({'measure': 'deaths', 'age_low': low, 'age_high': high, 'sex': sex, 'location': location if location >= 0 else root_location, 'cause': 'all', 'value': len(sub_pop), 'sample_size': sample_size}, index=[0]).set_index(['measure', 'age_low', 'age_high', 'sex', 'location', 'cause']))
        return cube