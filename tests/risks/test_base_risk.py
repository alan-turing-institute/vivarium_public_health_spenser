import pytest

import os
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.stats import norm

from ceam import config
from ceam.config_tree import ConfigTree
from ceam.framework.event import listens_for
from ceam.framework.population import uses_columns
from ceam.framework.util import from_yearly
from ceam.interpolation import Interpolation
from ceam.test_util import setup_simulation, pump_simulation, build_table, generate_test_population

from ceam_inputs import risk_factors, causes

from ceam_public_health.experiments.cvd.components import heart_disease, stroke

from ceam_public_health.risks import distributions, exposures
from ceam_public_health.risks.effect import continuous_exposure_effect, categorical_exposure_effect, RiskEffect
from ceam_public_health.risks.exposures import basic_exposure_function
from ceam_public_health.risks.distributions import distribution_map
from ceam_public_health.risks.base_risk import (CategoricalRiskComponent, ContinuousRiskComponent,
                                                correlated_propensity, uncorrelated_propensity)

def setup():
    try:
        config.reset_layer('override', preserve_keys=['input_data.intermediary_data_cache_path',
                                                      'input_data.auxiliary_data_folder'])
    except KeyError:
        pass
    config.simulation_parameters.set_with_metadata('year_start', 1990, layer='override',
                                                   source=os.path.realpath(__file__))
    config.simulation_parameters.set_with_metadata('year_end', 2010, layer='override',
                                                   source=os.path.realpath(__file__))
    config.simulation_parameters.set_with_metadata('time_step', 30.5, layer='override',
                                                   source=os.path.realpath(__file__))

def test_RiskEffect():
    config.simulation_parameters.time_step = 30.5
    time_step = timedelta(days=30.5)
    test_exposure = [0]

    def test_function(rates_, rr):
        return rates_ * (rr.values**test_exposure[0])

    effect = RiskEffect(build_table(1.01), build_table(0.01), 0, causes.heart_attack, test_function)

    simulation = setup_simulation([generate_test_population, effect])

    # This one should be effected by our RiskEffect
    rates = simulation.values.get_rate('incidence_rate.'+causes.heart_attack.name)
    rates.source = simulation.tables.build_table(build_table(0.01))

    # This one should not
    other_rates = simulation.values.get_rate('incidence_rate.some_other_cause')
    other_rates.source = simulation.tables.build_table(build_table(0.01))

    assert np.allclose(rates(simulation.population.population.index), from_yearly(0.01, time_step))
    assert np.allclose(other_rates(simulation.population.population.index), from_yearly(0.01, time_step))

    test_exposure[0] = 1

    assert np.allclose(rates(simulation.population.population.index), from_yearly(0.0101, time_step))
    assert np.allclose(other_rates(simulation.population.population.index), from_yearly(0.01, time_step))


def make_dummy_column(name, initial_value):
    @listens_for('initialize_simulants')
    @uses_columns([name])
    def make_column(event):
        event.population_view.update(pd.Series(initial_value, index=event.index, name=name))
    return make_column


def test_continuous_exposure_effect():
    risk = risk_factors.high_systolic_blood_pressure
    exposure_function = continuous_exposure_effect(risk)

    simulation = setup_simulation([generate_test_population,
                                   make_dummy_column(risk.name+'_exposure', risk.tmrl), exposure_function])

    rates = pd.Series(0.01, index=simulation.population.population.index)
    rr = pd.Series(1.01, index=simulation.population.population.index)

    assert np.all(exposure_function(rates, rr) == 0.01)

    simulation.population.get_view([risk.name+'_exposure']).update(
        pd.Series(risk.tmrl + 50, index=simulation.population.population.index))

    expected_value = 0.01 * (1.01 ** (((risk.tmrl + 50) - risk.tmrl) / risk.scale))

    assert np.allclose(exposure_function(rates, rr), expected_value)


def test_categorical_exposure_effect():
    risk = risk_factors.high_systolic_blood_pressure
    exposure_function = categorical_exposure_effect(risk)

    simulation = setup_simulation([generate_test_population,
                                   make_dummy_column(risk.name+'_exposure', 'cat2'), exposure_function])

    rates = pd.Series(0.01, index=simulation.population.population.index)
    rr = pd.DataFrame({'cat1': 1.01, 'cat2': 1}, index=simulation.population.population.index)

    assert np.all(exposure_function(rates, rr) == 0.01)

    simulation.population.get_view([risk.name+'_exposure']).update(
        pd.Series('cat1', index=simulation.population.population.index))

    assert np.allclose(exposure_function(rates, rr), 0.0101)


@patch('ceam_public_health.risks.effect.inputs')
@patch('ceam_public_health.risks.base_risk.inputs')
def test_CategoricalRiskComponent_dichotomous_case(br_inputs_mock, effect_inputs_mock):
    time_step = timedelta(days=30.5)
    config.simulation_parameters.time_step = 30.5
    risk = risk_factors.smoking_prevalence_approach

    br_inputs_mock.get_exposures.side_effect = lambda *args, **kwargs: build_table(
        0.5, ['age', 'year', 'sex', 'cat1', 'cat2'])
    effect_inputs_mock.get_relative_risks.side_effect = lambda *args, **kwargs: build_table(
        [1.01, 1], ['age', 'year', 'sex', 'cat1', 'cat2'])

    effect_inputs_mock.get_pafs.side_effect = lambda *args, **kwargs: build_table(1)
    effect_inputs_mock.get_mediation_factors = lambda *args, **kwargs: 0

    component = CategoricalRiskComponent(risk)

    simulation = setup_simulation([generate_test_population, component], 100000)
    pump_simulation(simulation, iterations=1)

    incidence_rate = simulation.values.get_rate('incidence_rate.'+risk.effected_causes[0].name)
    incidence_rate.source = simulation.tables.build_table(build_table(0.01))
    paf = simulation.values.get_rate('paf.'+risk.effected_causes[-1].name)

    assert np.isclose((simulation.population.population[risk.name+'_exposure'] == 'cat1').sum()
                      / len(simulation.population.population), 0.5, rtol=0.01)

    expected_exposed_value = 0.01 * 1.01
    expected_unexposed_value = 0.01

    exposed_index = simulation.population.population.index[
        simulation.population.population[risk.name+'_exposure'] == 'cat1']
    unexposed_index = simulation.population.population.index[
        simulation.population.population[risk.name+'_exposure'] == 'cat2']

    assert np.allclose(incidence_rate(exposed_index), from_yearly(expected_exposed_value, time_step))
    assert np.allclose(incidence_rate(unexposed_index), from_yearly(expected_unexposed_value, time_step))


@patch('ceam_public_health.risks.effect.inputs')
@patch('ceam_public_health.risks.base_risk.inputs')
def test_CategoricalRiskComponent_polydomous_case(br_inputs_mock, effect_inputs_mock):
    time_step = timedelta(days=30.5)
    config.simulation_parameters.time_step = 30.5
    risk = risk_factors.smoking_prevalence_approach
    br_inputs_mock.get_exposures.side_effect = lambda *args, **kwargs: build_table(
        0.25, ['age', 'year', 'sex', 'cat1', 'cat2', 'cat3', 'cat4'])
    effect_inputs_mock.get_relative_risks.side_effect = lambda *args, **kwargs: build_table(
        [1.03, 1.02, 1.01, 1], ['age', 'year', 'sex', 'cat1', 'cat2', 'cat3', 'cat4'])
    effect_inputs_mock.get_pafs.side_effect = lambda *args, **kwargs: build_table(1)
    effect_inputs_mock.get_mediation_factors = lambda *args, **kwargs: 0

    component = CategoricalRiskComponent(risk)

    simulation = setup_simulation([generate_test_population, component], 100000)
    pump_simulation(simulation, iterations=1)

    incidence_rate = simulation.values.get_rate('incidence_rate.'+risk.effected_causes[0].name)
    incidence_rate.source = simulation.tables.build_table(build_table(0.01))
    paf = simulation.values.get_rate('paf.'+risk.effected_causes[-1].name)

    for category in ['cat1', 'cat2', 'cat3', 'cat4']:
        assert np.isclose((simulation.population.population[risk.name+'_exposure'] == category).sum()
                          / len(simulation.population.population), 0.25, rtol=0.02)

    expected_exposed_value = 0.01 * np.array([1.02, 1.03, 1.01])

    for cat, expected in zip(['cat1','cat2','cat3','cat4'], expected_exposed_value):
        exposed_index = simulation.population.population.index[simulation.population.population[risk.name+'_exposure'] == cat]
        assert np.allclose(incidence_rate(exposed_index), from_yearly(expected, time_step), rtol=0.01)


@patch('ceam_public_health.risks.base_risk.get_exposure_function')
@patch('ceam_public_health.risks.base_risk.get_distribution')
@patch('ceam_public_health.risks.effect.inputs')
@patch('ceam_public_health.risks.base_risk.inputs')
def test_ContinuousRiskComponent(br_inputs_mock, effect_inputs_mock, get_distribution_mock, get_exposure_function_mock):
    time_step = timedelta(days=30.5)
    risk = risk_factors.high_systolic_blood_pressure
    br_inputs_mock.get_exposures.side_effect = lambda *args, **kwargs: build_table(0.5)
    effect_inputs_mock.get_relative_risks.side_effect = lambda *args, **kwargs: build_table(1.01)
    effect_inputs_mock.get_pafs.side_effect = lambda *args, **kwargs: build_table(1)
    effect_inputs_mock.get_mediation_factors = lambda *args, **kwargs: 0

    def loader(builder):
        dist = Interpolation(
                build_table([130, 0.000001], ['age', 'year', 'sex', 'mean', 'std']),
                ['sex'],
                ['age', 'year'],
                func=lambda parameters: norm(loc=parameters['mean'], scale=parameters['std']).ppf)
        return builder.lookup(dist)

    get_distribution_mock.side_effect = lambda *args, **kwargs: loader
    get_exposure_function_mock.side_effect = lambda *args, **kwargs: basic_exposure_function

    component = ContinuousRiskComponent(risk)

    simulation = setup_simulation([generate_test_population, component], 100000)
    pump_simulation(simulation, iterations=1)

    incidence_rate = simulation.values.get_rate('incidence_rate.'+risk.effected_causes[0].name)
    incidence_rate.source = simulation.tables.build_table(build_table(0.01))
    paf = simulation.values.get_rate('paf.'+risk.effected_causes[-1].name)

    assert np.allclose(simulation.population.population[risk.name+'_exposure'], 130, rtol=0.001)

    expected_value = 0.01 * (1.01**((130 - 112) / 10))

    assert np.allclose(incidence_rate(simulation.population.population.index),
                       from_yearly(expected_value, time_step), rtol=0.001)


@patch('ceam_public_health.risks.base_risk.get_exposure_function')
@patch('ceam_public_health.risks.base_risk.get_distribution')
@patch('ceam_public_health.risks.effect.inputs')
@patch('ceam_public_health.risks.base_risk.inputs')
def test_propensity_effect(br_inputs_mock, effect_inputs_mock, get_distribution_mock, get_exposure_function_mock):
    time_step = timedelta(days=30.5)
    risk = risk_factors.high_systolic_blood_pressure
    br_inputs_mock.get_exposures.side_effect = lambda *args, **kwargs: build_table(0.5)
    effect_inputs_mock.get_relative_risks.side_effect = lambda *args, **kwargs: build_table(1.01)
    effect_inputs_mock.get_pafs.side_effect = lambda *args, **kwargs: build_table(1)
    effect_inputs_mock.get_mediation_factors = lambda *args, **kwargs: 0

    def loader(builder):
        dist = Interpolation(
                build_table([130, 15], ['age', 'year', 'sex', 'mean', 'std']),
                ['sex'],
                ['age', 'year'],
                func=lambda parameters: norm(loc=parameters['mean'], scale=parameters['std']).ppf)
        return builder.lookup(dist)

    get_distribution_mock.side_effect = lambda *args, **kwargs: loader
    get_exposure_function_mock.side_effect = lambda *args, **kwargs: basic_exposure_function

    component = ContinuousRiskComponent(risk)


    simulation = setup_simulation([generate_test_population, component], 100000)
    pop_view = simulation.population.get_view([risk.name+'_propensity'])

    pop_view.update(pd.Series(0.00001, index=simulation.population.population.index))
    pump_simulation(simulation, iterations=1)

    expected_value = norm(loc=130, scale=15).ppf(0.00001)
    assert np.allclose(simulation.population.population[risk.name+'_exposure'], expected_value)

    pop_view.update(pd.Series(0.5, index=simulation.population.population.index))
    pump_simulation(simulation, iterations=1)

    expected_value = 130
    assert np.allclose(simulation.population.population[risk.name+'_exposure'], expected_value)

    pop_view.update(pd.Series(0.99999, index=simulation.population.population.index))
    pump_simulation(simulation, iterations=1)

    expected_value = norm(loc=130, scale=15).ppf(0.99999)
    assert np.allclose(simulation.population.population[risk.name+'_exposure'], expected_value)


@patch('ceam_public_health.risks.base_risk.inputs')
def test_correlated_propensity(inputs_mock):
    correlation_matrix = pd.DataFrame({
        'high_systolic_blood_pressure':           [1, 0.282213017344475, 0.110525231808424, 0.130475437755401, 0.237914389663941],
        'high_body_mass_index':                   [0.282213017344475, 1, 0.0928986519575119, -0.119147761153339, 0.212531763837137],
        'high_total_cholesterol':                 [0.110525231808424, 0.0928986519575119, 1, 0.175454370605231, 0.0476387962101613],
        'smoking_prevalence_approach':            [0.130475437755401, -0.119147761153339, 0.175454370605231, 1, 0.0770317213079334],
        'high_fasting_plasma_glucose_continuous': [0.237914389663941, 0.212531763837137, 0.0476387962101613, 0.0770317213079334, 1],
        'risk_factor':                ['high_systolic_blood_pressure', 'high_body_mass_index',
                                       'high_total_cholesterol', 'smoking_prevalence_approach',
                                       'high_fasting_plasma_glucose_continuous'],
        })
    correlation_matrix['age'] = 30
    correlation_matrix['sex'] = 'Male'
    inputs_mock.load_risk_correlation_matrices.return_value = correlation_matrix

    pop = pd.DataFrame({'age': [30]*100000, 'sex': ['Male']*100000})

    propensities = []
    for risk in [
            risk_factors.high_systolic_blood_pressure,
            risk_factors.high_body_mass_index,
            risk_factors.high_total_cholesterol,
            risk_factors.smoking_prevalence_approach,
            risk_factors.high_fasting_plasma_glucose_continuous]:
        propensities.append(correlated_propensity(pop, risk))

    matrix = np.corrcoef(np.array(propensities))
    assert np.allclose(correlation_matrix[['high_systolic_blood_pressure', 'high_body_mass_index',
                                           'high_total_cholesterol', 'smoking_prevalence_approach',
                                           'high_fasting_plasma_glucose_continuous']].values, matrix, rtol=0.15)


def test_uncorrelated_propensity():
    pop = pd.DataFrame({'age': [30]*1000000, 'sex': ['Male']*1000000})
    propensities = []
    for risk in [
            risk_factors.high_systolic_blood_pressure,
            risk_factors.high_body_mass_index,
            risk_factors.high_total_cholesterol,
            risk_factors.smoking_prevalence_approach,
            risk_factors.high_fasting_plasma_glucose_continuous]:
        propensities.append(uncorrelated_propensity(pop, risk))

    propensities = np.array(propensities)
    assert propensities.min() >= 0
    assert propensities.max() <= 1
    hist, _ = np.histogram(propensities, 100, density=True)
    assert np.all(np.abs(hist - 1) < 0.01)

def _fill_in_correlation_matrix(risk_order):
    matrix_base = pd.DataFrame({
        risk_order[0]: [1, 0.282213017344475, 0.110525231808424, 0.130475437755401],
        risk_order[1]: [0.282213017344475, 1, 0.0928986519575119, -0.119147761153339],
        risk_order[2]: [0.110525231808424, 0.0928986519575119, 1, 0.175454370605231],
        risk_order[3]: [0.130475437755401, -0.119147761153339, 0.175454370605231, 1],
        'risk_factor': risk_order,
        })
    correlation_matrix = pd.DataFrame()

    for age in range(0,120, 5):
        for sex in ['Male', 'Female']:
            m = matrix_base.copy()
            m['age'] = age
            m['sex'] = sex
            correlation_matrix = correlation_matrix.append(m)

    return matrix_base, correlation_matrix


@pytest.mark.skip
@pytest.mark.slow
@patch('ceam_public_health.risks.base_risk.inputs.load_risk_correlation_matrices')
def test_correlated_exposures(correlation_mock):
    from rpy2.robjects import r, pandas2ri, numpy2ri
    pandas2ri.activate()
    numpy2ri.activate()
    #config.simulation_parameters.pop_age_start = 30
    #config.simulation_parameters.pop_age_end = 100
    config.simulation_parameters.initial_age = 50
    categorical_risks = [risk_factors.no_access_to_handwashing_facility, risk_factors.smoking_prevalence_approach]
    continuous_risks = [risk_factors.high_systolic_blood_pressure, risk_factors.high_total_cholesterol]

    categorical_risk_order = [r.name for r in categorical_risks]
    continuous_risk_order = [r.name for r in continuous_risks]
    risk_order = categorical_risk_order + continuous_risk_order


    matrix_base, correlation_mock.return_value = _fill_in_correlation_matrix(risk_order)

    observations = []
    for i in range(100):
        print('running {}'.format(i))
        config.run_configuration.draw_number = i
        simulation = setup_simulation([generate_test_population] +
                                      [CategoricalRiskComponent(r, correlated_propensity) for r in categorical_risks] +
                                      [ContinuousRiskComponent(r, correlated_propensity) for r in continuous_risks],
                                     100000)

        pump_simulation(simulation, iterations=1)
        print('simulation done')

        r.source('/home/alecwd/Code/cost_effectiveness_misc/03_get_corr_matrix_function.R')
        pop = simulation.population.population[[c for c in simulation.population.population.columns if 'exposure' in c]]
        pop.columns = [c if 'exposure' not in c else c.rpartition('_')[0] for c in pop.columns]

        for risk in categorical_risks:
            pop[risk.name] = pop[risk.name].astype('category')

        pop = pop[risk_order]


        observations.append(pandas2ri.ri2py(r.get_corr_matrix(dat=pop, all_risks=r.c(*risk_order), dichotomous_risks=r.c(*categorical_risk_order))))
    assert np.all(np.abs(matrix_base[risk_order].values-np.array(observations).mean(axis=0)) <= np.array(observations).std(axis=0)*3)

def _mock_get_exposures(risk_id):
    e = {1: 0.5, 2: 0.25, 3: 0.1, 4: 0.8}[risk_id]
    if risk_id in [3, 4]:
        # Categorical risks
        return build_table([e, 1-e], columns=['age', 'year', 'sex', 'cat1', 'cat2'])
    else:
        return build_table(e)


def _mock_get_relative_risk(risk_id, cause_id):
    e = {1: 0, 2: 0, 3: 0, 4: 0}[risk_id]
    if risk_id in [3, 4]:
        # Categorical risks
        return build_table([e, e], columns=['age', 'year', 'sex', 'cat1', 'cat2'])
    else:
        return build_table(e)


def _mock_get_pafs(risk_id, cause_id):
    e = {1: 0, 2: 0, 3: 0, 4: 0}[risk_id]
    if risk_id in [3, 4]:
        # Categorical risks
        return build_table([e, e], columns=['age', 'year', 'sex', 'cat1', 'cat2'])
    else:
        return build_table(e)


@pytest.mark.skip
@patch('ceam_public_health.risks.base_risk.inputs')
def test_correlated_exposures_synthetic_risks(inputs_mock):
    from rpy2.robjects import r, pandas2ri, numpy2ri
    pandas2ri.activate()
    numpy2ri.activate()
    inputs_mock.load_risk_correlation_matrices.return_value = _fill_in_correlation_matrix()

    inputs_mock.get_exposures = _mock_get_exposures
    inputs_mock.get_relative_risk = _mock_get_relative_risk
    inputs_mock.get_pafs = _mock_get_pafs


    def loader(builder):
        dist = Interpolation(
                build_table([130, 15], ['age', 'year', 'sex', 'mean', 'std']),
                ['sex'],
                ['age', 'year'],
                func=lambda parameters: norm(loc=parameters['mean'], scale=parameters['std']).ppf)
        return builder.lookup(dist)

    continuous_1 = ConfigTree({'name': 'continuous_1', 'gbd_risk': 1, 'risk_type': 'continuous',
                               'effected_causes': [], 'tmrl': 112.5, 'scale': 10})
    continuous_2 = ConfigTree({'name': 'continuous_2', 'gbd_risk': 2, 'risk_type': 'continuous',
                               'effected_causes': [], 'tmrl': 3.08, 'scale': 1})
    categorical_1 = ConfigTree({'name': 'categorical_1', 'gbd_risk': 3, 'effected_causes': [], 'risk_type': 'categorical'})
    categorical_2 = ConfigTree({'name': 'categorical_2', 'gbd_risk': 4, 'effected_causes': [], 'risk_type': 'categorical'})

    distribution_map[continuous_1.name] = loader
    distribution_map[continuous_2.name] = loader
    distribution_map[continuous_1.name] = loader
    distribution_map[continuous_2.name] = loader

    continuous_1_component = ContinuousRiskComponent(continuous_1, correlated_propensity)
    continuous_2_component = ContinuousRiskComponent(continuous_2, correlated_propensity)
    categorical_1_component = CategoricalRiskComponent(categorical_1, correlated_propensity)
    categorical_2_component = CategoricalRiskComponent(categorical_2, correlated_propensity)
    simulation = setup_simulation([generate_test_population, continuous_1_component,
                                   continuous_2_component, categorical_1_component, categorical_2_component], 10000)

    pump_simulation(simulation, iterations=1)

    r.source('/home/alecwd/Code/cost_effectiveness_misc/03_get_corr_matrix_function.R')
    pop = simulation.population.population[[c for c in simulation.population.population.columns if 'exposure' in c]]
    pop.columns = [c if 'exposure' not in c else c.rpartition('_')[0] for c in pop.columns]
    pop['categorical_1'] = pop.categorical_1.astype('category')
    pop['categorical_2'] = pop.categorical_2.astype('category')
    from rpy2.rinterface import RRuntimeError
    failure = None

    try:
        observed_correlation = pandas2ri.ri2py(r.get_corr_matrix(dat=pop, all_risks=r.c("continuous_1","continuous_2", "categorical_1","categorical_2"), dichotomous_risks=r.c('categorical_1','categorical_2')))
    except Exception as e:
        print('test')
        print(e)
        print('\n'.join(r('unlist(traceback())')))
    assert np.allclose(matrix_base[["continuous_1","continuous_2", "categorical_1","categorical_2"]].values, observed_correlation, rtol=0.25)


class RiskMock:
    def __init__(self, risk, risk_effect, distribution_loader, exposure_function=basic_exposure_function):
        if isinstance(distribution_loader, str):
            module_path, _, name = distribution_loader.rpartition('.')
            distribution_loader = getattr(import_module(module_path), name)

        if isinstance(exposure_function, str):
            module_path, _, name = exposure_function.rpartition('.')
            exposure_function = getattr(import_module(module_path), name)

        self._risk = risk_factors[risk] if isinstance(risk, str) else risk
        self.risk_effect = risk_effect
        self._distribution_loader = distribution_loader
        self.exposure_function = exposure_function

    def setup(self, builder):
        self.distribution = self._distribution_loader(builder)
        self.randomness = builder.randomness(self._risk.name)
        self.population_view = builder.population_view([self._risk.name+'_exposure', self._risk.name+'_propensity'])

        return [self.risk_effect]

    @listens_for('initialize_simulants')
    @uses_columns(['age', 'sex'])
    def load_population_columns(self, event):
        propensities = pd.Series(uncorrelated_propensity(event.population, self._risk),
                                 name=self._risk.name + '_propensity',
                                 index=event.index)
        self.population_view.update(propensities)
        self.population_view.update(pd.Series(self.exposure_function(propensities, self.distribution(event.index)),
                                              name=self._risk.name + '_exposure',
                                              index=event.index))

    @listens_for('time_step__prepare', priority=8)
    def update_exposure(self, event):
        population = self.population_view.get(event.index)
        distribution = self.distribution(event.index)
        new_exposure = self.exposure_function(population[self._risk.name + '_propensity'], distribution)
        self.population_view.update(pd.Series(new_exposure, name=self._risk.name + '_exposure', index=event.index))


def test_make_gbd_risk_effects():
    time_step = config.simulation_parameters.time_step
    # adjusted pafs
    paf = 0.01
    mediation_factor = 0.6
    effect_function = continuous_exposure_effect(risk_factors.high_systolic_blood_pressure)
    risk_effect = RiskEffect(rr_data=build_table(0),
                             paf_data=build_table(paf),
                             mediation_factor=mediation_factor,
                             cause=causes.heart_attack,
                             exposure_effect=effect_function)
    sbp = RiskMock(risk_factors.high_systolic_blood_pressure, risk_effect,
                   distributions.sbp, exposures.sbp)
    simulation = setup_simulation([generate_test_population, heart_disease.factory(), sbp])
    pafs = simulation.values.get_value('paf.heart_attack')
    assert np.allclose(pafs(simulation.population.population.index), paf * (1 - mediation_factor))

    paf = 0.25
    mediation_factor = 0.4
    effect_function = continuous_exposure_effect(risk_factors.high_systolic_blood_pressure)
    risk_effect = RiskEffect(rr_data=build_table(0),
                             paf_data=build_table(paf),
                             mediation_factor=mediation_factor,
                             cause=causes.ischemic_stroke,
                             exposure_effect=effect_function)
    sbp = RiskMock(risk_factors.high_systolic_blood_pressure, risk_effect,
                   distributions.sbp, exposures.sbp)
    simulation = setup_simulation([generate_test_population, stroke.factory(), sbp])
    pafs = simulation.values.get_value('paf.ischemic_stroke')
    assert np.allclose(pafs(simulation.population.population.index), paf * (1 - mediation_factor))

    paf = 0.9
    mediation_factor = 0.02
    effect_function = continuous_exposure_effect(risk_factors.high_body_mass_index)
    risk_effect = RiskEffect(rr_data=build_table(0),
                             paf_data=build_table(paf),
                             mediation_factor=mediation_factor,
                             cause=causes.hemorrhagic_stroke,
                             exposure_effect=effect_function)
    bmi = RiskMock(risk_factors.high_body_mass_index, risk_effect,
                   distributions.bmi)
    simulation = setup_simulation([generate_test_population, stroke.factory(), bmi])
    pafs = simulation.values.get_value('paf.hemorrhagic_stroke')
    assert np.allclose(pafs(simulation.population.population.index), paf * (1 - mediation_factor))

    # adjusted rrs
    rr = 1.26
    mediation_factor = 0.5
    adjrr = rr ** (1 - mediation_factor)
    tmrl = 21
    scale = 5
    exposure = 30

    effect_function = continuous_exposure_effect(risk_factors.high_body_mass_index)
    risk_effect = RiskEffect(rr_data=build_table(rr),
                             paf_data=build_table(0),
                             mediation_factor=mediation_factor,
                             cause=causes.heart_attack,
                             exposure_effect=effect_function)
    bmi = RiskMock(risk_factors.high_body_mass_index, risk_effect,
                   distributions.bmi,
                   exposure_function=lambda propensity, distribution: pd.Series(exposure, index=propensity.index))
    simulation = setup_simulation([generate_test_population, heart_disease.factory(), bmi])
    irs = simulation.values.get_value('incidence_rate.heart_attack')
    base_ir = irs.source(simulation.population.population.index)

    assert np.allclose(irs(simulation.population.population.index),
                       base_ir * max(adjrr**((exposure-tmrl)/scale), 1) * time_step/365, rtol=0.05)