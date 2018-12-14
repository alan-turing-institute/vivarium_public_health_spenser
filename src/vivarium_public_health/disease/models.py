import pandas as pd

from vivarium_inputs.utilities import DataMissingError
from vivarium_public_health.disease import (SusceptibleState, ExcessMortalityState, RecoveredState,
                                            DiseaseState, DiseaseModel)


def get_aggregate_disability_weight(cause: str, builder):
    """Calculates the cause-level disability weight as the sum of the causes's sequelae's disability weights
    weighted by their prevalences.

    Parameters
    ----------
    cause: str
        A cause name
    builder:
        A vivarium builder object

    Returns
    -------
        float
    """
    aggregate_dw = 0.0
    sequelae = builder.data.load(f"cause.{cause}.sequelae")
    for s in sequelae:

        prevalence = builder.data.load(f"sequela.{s}.prevalence")
        try:
            disability_weight = builder.data.load(f"sequela.{s}.disability_weight")
            assert disability_weight.shape[0] == 1
            disability_weight = disability_weight.value
        except DataMissingError:
            disability_weight = 0.0
        aggregate_dw = prevalence.copy()
        aggregate_dw['value'] *= disability_weight

    return aggregate_dw


class SI:

    def __init__(self, cause: str):
        self.cause = cause

    def setup(self, builder):
        # TODO: Are we sure about this? Do we neversupport morbidity + mortality ?
        only_morbid = builder.data.load(f'cause.{self.cause}.restrictions')['yld_only']

        healthy = SusceptibleState(self.cause)
        healthy.allow_self_transitions()

        get_data_functions = {}
        if only_morbid:
            infected = DiseaseState(self.cause,
                                    get_data_functions={'disability_weight': get_aggregate_disability_weight})
            get_data_functions['csmr'] = lambda _, __: None  # DiseaseModel will try to pull not provided
        else:
            infected = ExcessMortalityState(self.cause,
                                            get_data_functions={'disability_weight': get_aggregate_disability_weight})
        infected.allow_self_transitions()

        healthy.add_transition(infected, source_data_type='rate')

        builder.components.add_components([DiseaseModel(self.cause, states=[healthy, infected],
                                                        get_data_functions=get_data_functions)])


class SIR:

    def __init__(self, cause: str):
        self.cause = cause

    def setup(self, builder):
        only_morbid = builder.data.load(f'cause.{self.cause}.restrictions')['yld_only']

        healthy = SusceptibleState(self.cause)
        healthy.allow_self_transitions()

        get_data_functions = {}
        if only_morbid:
            infected = DiseaseState(self.cause,
                                    get_data_functions={'disability_weights': get_aggregate_disability_weight})
            get_data_functions['csmr'] = lambda _, __: None
        else:
            infected = ExcessMortalityState(self.cause,
                                            get_data_functions={'disability_weight': get_aggregate_disability_weight})
        infected.allow_self_transitions()

        recovered = RecoveredState(self.cause)
        recovered.allow_self_transitions()

        healthy.add_transition(infected, source_data_type='rate')
        infected.add_transition(recovered, source_data_type='rate')

        builder.components.add_components([DiseaseModel(self.cause, states=[healthy, infected, recovered])])


class SIS:

    def __init__(self, cause):
        self.cause = cause

    def setup(self, builder):
        only_morbid = builder.data.load(f'cause.{self.cause}.restrictions')['yld_only']

        healthy = SusceptibleState(self.cause)
        healthy.allow_self_transitions()

        get_data_functions = {}
        if only_morbid:
            infected = DiseaseState(self.cause,
                                    get_data_functions={'disability_weights': get_aggregate_disability_weight})
            get_data_functions['csmr'] = lambda _, __: None
        else:
            infected = ExcessMortalityState(self.cause,
                                            get_data_functions={'disability_weight': get_aggregate_disability_weight})
        infected.allow_self_transitions()

        healthy.add_transition(infected, source_data_type='rate')
        infected.add_transition(healthy, source_data_type='rate')

        builder.components.add_components([DiseaseModel(self.cause, states=[healthy, infected])])


class SIS_fixed_duration:

    def __init__(self, cause, duration):
        self.cause = cause
        if not isinstance(duration, pd.Timedelta):
            self.duration = pd.Timedelta(days=float(duration) // 1, hours=float(duration) % 1)
        else:
            self.duration = duration

    def setup(self, builder):
        only_morbid = builder.data.load(f'cause.{self.cause}.restrictions')['yld_only']

        healthy = SusceptibleState(self.cause)
        healthy.allow_self_transitions()

        get_data_functions = {}
        if only_morbid:
            infected = DiseaseState(self.cause,
                                    get_data_functions={'disability_weight': get_aggregate_disability_weight,
                                                        'dwell_time': lambda _, __: self.duration})
            get_data_functions['csmr'] = lambda _, __: None
        else:
            infected = ExcessMortalityState(self.cause,
                                            get_data_functions={'disability_weight': get_aggregate_disability_weight,
                                                                'dwell_time': lambda _, __: self.duration})
        infected.allow_self_transitions()

        healthy.add_transition(infected, source_data_type='rate')
        infected.add_transition(healthy)

        builder.components.add_components([DiseaseModel(self.cause, states=[healthy, infected])])


class neonatal:

    def __init__(self, cause):
        self.cause = cause

    def setup(self, builder):

        only_morbid = builder.data.load(f'cause.{self.cause}.restricitons')['yld_only']

        healthy = SusceptibleState(self.cause)
        healthy.allow_self_transitions()

        get_data_functions = {}
        if only_morbid:
            with_condition = DiseaseState(self.cause,
                                          get_data_functions={'disability_weight': get_aggregate_disability_weight})
            get_data_functions['csmr'] = lambda _, __: None
        else:
            with_condition = ExcessMortalityState(self.cause,
                                                  get_data_functions={'disability_weight': get_aggregate_disability_weight})
        with_condition.allow_self_transitions()

        # TODO: some neonatal causes (e.g. sepsis) have incidence and remission at least at the MEID level
        # healthy.add_transition(with_condition, source_data_type='rate')
        # with_condition.add_transition(healthy, source_data_type='rate')

        builder.components.add_components([DiseaseModel(self.cause, states=[healthy, with_condition])])






