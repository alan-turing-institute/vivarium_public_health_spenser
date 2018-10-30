import numpy as np
import pandas as pd

from distributions import base_distributions

from vivarium.framework.values import list_combiner, joint_value_post_processor
from vivarium_public_health.util import pivot_age_sex_year_binned
from .data_transformation import should_rebin, rebin_exposure_data


class MissingDataError(Exception):
    pass


class EnsembleSimulation(base_distributions.EnsembleDistribution):
    def setup(self, builder):
        builder.components.add_components(self._distributions.values())


class SimulationDistribution:
    def __init__(self, data, distribution):
        self.distribution = distribution
        self._parameters = base_distributions.get_params(data, self.distribution)

    def setup(self, builder):
        self.parameters = {name: builder.lookup.build_table(data.reset_index())
                           for name, data in self._parameters.items()}

    def ppf(self, x):
        params = {name: p(x.index) for name, p in self.parameters.items()}
        return self.distribution(params).ppf(x)


class PolytomousDistribution:
    def __init__(self, exposure_data: pd.DataFrame, risk: str):
        self.exposure_data = exposure_data
        self._risk = risk
        self.categories = sorted([column for column in self.exposure_data if 'cat' in column],
                                 key=lambda column: int(column[3:]))

    def setup(self, builder):
        self.exposure = builder.value.register_value_producer(f'{self._risk}.exposure_parameters',
                                                              source=builder.lookup.build_table(self.exposure_data))

    def ppf(self, x):
        exposure = self.exposure(x.index)
        sorted_exposures = exposure[self.categories]
        if not np.allclose(1, np.sum(sorted_exposures, axis=1)):
            raise MissingDataError('All exposure data returned as 0.')
        exposure_sum = sorted_exposures.cumsum(axis='columns')
        category_index = (exposure_sum.T < x).T.sum('columns')
        return pd.Series(np.array(self.categories)[category_index], name=self._risk + '_exposure', index=x.index)


class DichotomousDistribution:
    def __init__(self, exposure_data: pd.DataFrame, risk: str):
        self.exposure_data = exposure_data.drop('cat2', axis=1)
        self._risk = risk

    def setup(self, builder):
        self._base_exposure = builder.lookup.build_table(self.exposure_data)
        self.exposure_proportion = builder.value.register_value_producer(f'{self._risk}.exposure_parameters',
                                                                         source=self.exposure)
        self.joint_paf = builder.value.register_value_producer(f'{self._risk}.paf',
                                                               source=lambda index: [pd.Series(0, index=index)],
                                                               preferred_combiner=list_combiner,
                                                               preferred_post_processor=joint_value_post_processor)

    def exposure(self, index):
        base_exposure = self._base_exposure(index).values
        joint_paf = self.joint_paf(index).values
        return base_exposure * (1-joint_paf)

    def ppf(self, x):
        exposed = x < self.exposure_proportion(x.index)

        return pd.Series(exposed.replace({True: 'cat1', False: 'cat2'}), name=self._risk + '_exposure', index=x.index)


class RebinPolytomousDistribution(DichotomousDistribution):
    pass


def get_distribution(risk: str, risk_type: str, builder):

    distribution_type = builder.data.load(f"{risk_type}.{risk}.distribution")
    exposure_data = builder.data.load(f"{risk_type}.{risk}.exposure")

    if distribution_type == "dichotomous":
        exposure_data = pivot_age_sex_year_binned(exposure_data, 'parameter', 'value')
        distribution = DichotomousDistribution(exposure_data, risk)

    elif distribution_type == 'polytomous':
        SPECIAL = ['unsafe_water_source', 'low_birth_weight_and_short_gestation']
        rebin = should_rebin(risk, builder.configuration)

        if rebin and risk in SPECIAL:
            raise NotImplementedError(f'{risk} cannot be rebinned at this point')

        if rebin:
            exposure_data = rebin_exposure_data(exposure_data)
            exposure_data = pivot_age_sex_year_binned(exposure_data, 'parameter', 'value')
            distribution = RebinPolytomousDistribution(exposure_data, risk)
        else:
            exposure_data = pivot_age_sex_year_binned(exposure_data, 'parameter', 'value')
            distribution = PolytomousDistribution(exposure_data, risk)

    elif distribution_type in ['normal', 'lognormal', 'ensemble']:
        exposure_sd = builder.data.load(f"{risk_type}.{risk}.exposure_standard_deviation")
        exposure_data = exposure_data.rename(index=str, columns={"value": "mean"})
        exposure_sd = exposure_sd.rename(index=str, columns={"value": "standard_deviation"})

        exposure = exposure_data.merge(exposure_sd).set_index(['year', 'year_start', 'year_end',
                                                               'age', 'age_group_start', 'age_group_end', 'sex'])

        if distribution_type == 'normal':
            distribution = SimulationDistribution(exposure, base_distributions.Normal)

        elif distribution_type == 'lognormal':
            distribution = SimulationDistribution(exposure, base_distributions.LogNormal)

        else:
            # weights = builder.data.load(f'risk_factor.{risk}.ensemble_weights')
            # distribution_map = {'betasr': BetaSimulation,
            #                     'exp': ExponentialSimulation,
            #                     'gamma': GammaSimulation,
            #                     'gumbel': GumbelSimulation,
            #                     'invgamma': InverseGammaSimulation,
            #                     'invweibull': InverseWeibullSimulation,
            #                     'llogis': LogLogisticSimulation,
            #                     'lnorm': LogNormalSimulation,
            #                     'mgamma': MirroredGammaSimulation,
            #                     'mgumbel': MirroredGumbelSimulation,
            #                     'norm': NormalSimulation,
            #                     'weibull': WeibullSimulation}
            #
            # if risk == 'high_ldl_cholesterol':
            #     weights = weights.drop('invgamma', axis=1)
            #
            # if 'invweibull' in weights.columns and np.all(weights['invweibull'] < 0.05):
            #     weights = weights.drop('invweibull', axis=1)
            #
            # weights_cols = list(set(distribution_map.keys()) & set(weights.columns))
            # weights = weights[weights_cols]
            #
            # # weight is all same across the demo groups
            # e_weights = weights.iloc[0]
            # dist = {d: distribution_map[d] for d in weights_cols}
            #
            # distribution = EnsembleSimulation(exposure, e_weights/np.sum(e_weights), dist)
            distribution = None

    else:
        raise NotImplementedError(f"Unhandled distribution type {distribution}")

    return distribution
