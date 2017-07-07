from ceam_inputs import (get_incidence, get_post_mi_heart_failure_proportion_draws,
                         get_angina_proportions, get_asympt_ihd_proportions, causes, get_cause_specific_mortality)

from ceam_public_health.disease import DiseaseState, TransientDiseaseState, make_disease_state, DiseaseModel
from ceam_public_health.treatment import hospitalization_side_effect_factory


def factory():
    healthy = DiseaseState('healthy', track_events=False, key='ihd')
    healthy.allow_self_transitions()

    heart_attack = make_disease_state(causes.heart_attack,
                                      dwell_time=28,
                                      side_effect_function=hospitalization_side_effect_factory(
                                          0.6, 0.7, 'heart attack'))  # rates as per Marcia e-mail 1/19/17

    heart_failure = TransientDiseaseState('heart_failure', track_events=False)
    mild_heart_failure = make_disease_state(causes.mild_heart_failure)
    moderate_heart_failure = make_disease_state(causes.moderate_heart_failure)
    severe_heart_failure = make_disease_state(causes.severe_heart_failure)

    angina = TransientDiseaseState('non_mi_angina', track_events=False)
    asymptomatic_angina = make_disease_state(causes.asymptomatic_angina)
    mild_angina = make_disease_state(causes.mild_angina)
    moderate_angina = make_disease_state(causes.moderate_angina)
    severe_angina = make_disease_state(causes.severe_angina)

    asymptomatic_ihd = make_disease_state(causes.asymptomatic_ihd)

    healthy.add_transition(heart_attack, rates=get_incidence(causes.heart_attack.incidence))
    heart_failure.add_transition(mild_heart_failure, proportion=0.182074)
    heart_failure.add_transition(moderate_heart_failure, proportion=0.149771)
    heart_failure.add_transition(severe_heart_failure, proportion=0.402838)

    healthy.add_transition(angina, rates=get_incidence(causes.angina_not_due_to_MI.incidence))
    angina.add_transition(asymptomatic_angina, proportion=0.304553)
    angina.add_transition(mild_angina, proportion=0.239594)
    angina.add_transition(moderate_angina, proportion=0.126273)
    angina.add_transition(severe_angina, proportion=0.32958)

    # TODO: Need to figure out best way to implement functions here
    # TODO: Need to figure out where transition from rates to probabilities needs to happen
    hf_prop_df = get_post_mi_heart_failure_proportion_draws()
    angina_prop_df = get_angina_proportions()
    asympt_prop_df = get_asympt_ihd_proportions()

    # post-mi transitions
    # TODO: Figure out if we can pass in me_id here to get incidence for the correct cause of heart failure
    # TODO: Figure out how to make asymptomatic ihd be equal to
    # whatever is left after people get heart failure and angina
    heart_attack.add_transition(heart_failure, proportion=hf_prop_df)
    heart_attack.add_transition(angina, proportion=angina_prop_df)
    heart_attack.add_transition(asymptomatic_ihd, proportion=asympt_prop_df)

    heart_attack_incidence = get_incidence(causes.heart_attack.incidence)
    for sequela in [mild_heart_failure, moderate_heart_failure, severe_heart_failure, asymptomatic_angina,
                    mild_angina, moderate_angina, severe_angina, asymptomatic_ihd]:
        sequela.add_transition(heart_attack, rates=heart_attack_incidence)

    return DiseaseModel('ihd',
                        states=[healthy,
                                heart_attack,
                                asymptomatic_ihd,
                                heart_failure, mild_heart_failure, moderate_heart_failure, severe_heart_failure,
                                angina, asymptomatic_angina, mild_angina, moderate_angina, severe_angina],
                        csmr_data=get_cause_specific_mortality(causes.heart_attack.gbd_cause))