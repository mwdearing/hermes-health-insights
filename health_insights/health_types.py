"""
Map bridge type_codes to metric display names and HealthKit identifiers.

The bridge stores display aliases (e.g. "weight", "steps", "heart_rate").
This module provides the inverse mapping used by the library so that
aggregates and reports carry stable, human-readable metric names plus the
canonical HealthKit identifier where known.
"""

# type_code -> (metric_display_name, healthkit_identifier)
# healthkit_identifier may be None when the bridge alias is not a standard HK type.
TYPE_MAP = {
    # Heart
    "heart_rate": ("Heart Rate", "HKQuantityTypeIdentifierHeartRate"),
    "resting_heart_rate": ("Resting Heart Rate", "HKQuantityTypeIdentifierRestingHeartRate"),
    "heart_rate_variability_sdnn": ("Heart Rate Variability SDNN", "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"),
    "heart_rate_variability_rmssd": ("HRV (RMSSD method)", None),
    "heart_rate_recovery_one_minute": ("Heart Rate Recovery One Minute", "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute"),
    "walking_heart_rate_average": ("Walking Heart Rate Average", "HKQuantityTypeIdentifierWalkingHeartRateAverage"),
    "recovery_score": ("Recovery Score", None),
    # Blood / Respiratory
    "oxygen_saturation": ("Oxygen Saturation", "HKQuantityTypeIdentifierOxygenSaturation"),
    "blood_glucose": ("Blood Glucose", "HKQuantityTypeIdentifierBloodGlucose"),
    "blood_pressure_systolic": ("Blood Pressure Systolic", "HKQuantityTypeIdentifierBloodPressureSystolic"),
    "blood_pressure_diastolic": ("Blood Pressure Diastolic", "HKQuantityTypeIdentifierBloodPressureDiastolic"),
    "respiratory_rate": ("Respiratory Rate", "HKQuantityTypeIdentifierRespiratoryRate"),
    "sleeping_breathing_disturbances": ("Sleeping Breathing Disturbances", "HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances"),
    "blood_alcohol_content": ("Blood Alcohol Content", "HKQuantityTypeIdentifierBloodAlcoholContent"),
    "peripheral_perfusion_index": ("Peripheral Perfusion Index", "HKQuantityTypeIdentifierPeripheralPerfusionIndex"),
    "forced_vital_capacity": ("Forced Vital Capacity", "HKQuantityTypeIdentifierForcedVitalCapacity"),
    "forced_expiratory_volume_1": ("Forced Expiratory Volume 1", "HKQuantityTypeIdentifierForcedExpiratoryVolume1"),
    "peak_expiratory_flow_rate": ("Peak Expiratory Flow Rate", "HKQuantityTypeIdentifierPeakExpiratoryFlowRate"),
    # Body
    "height": ("Height", "HKQuantityTypeIdentifierHeight"),
    "weight": ("Weight", "HKQuantityTypeIdentifierBodyMass"),
    "body_fat_percentage": ("Body Fat Percentage", "HKQuantityTypeIdentifierBodyFatPercentage"),
    "body_mass_index": ("Body Mass Index", "HKQuantityTypeIdentifierBodyMassIndex"),
    "lean_body_mass": ("Lean Body Mass", "HKQuantityTypeIdentifierLeanBodyMass"),
    "body_temperature": ("Body Temperature", "HKQuantityTypeIdentifierBodyTemperature"),
    "skin_temperature": ("Wrist Temperature", "HKQuantityTypeIdentifierAppleSleepingWristTemperature"),
    "waist_circumference": ("Waist Circumference", "HKQuantityTypeIdentifierWaistCircumference"),
    "body_fat_mass": ("Body Fat Mass", None),
    "skeletal_muscle_mass": ("Skeletal Muscle Mass", None),
    # Fitness
    "vo2_max": ("VO2 Max", "HKQuantityTypeIdentifierVO2Max"),
    "six_minute_walk_test_distance": ("Six Minute Walk Test Distance", "HKQuantityTypeIdentifierSixMinuteWalkTestDistance"),
    # Activity
    "steps": ("Steps", "HKQuantityTypeIdentifierStepCount"),
    "energy": ("Active Energy", "HKQuantityTypeIdentifierActiveEnergyBurned"),
    "basal_energy": ("Basal Energy", "HKQuantityTypeIdentifierBasalEnergyBurned"),
    "stand_time": ("Stand Time", "HKQuantityTypeIdentifierAppleStandTime"),
    "exercise_time": ("Exercise Time", "HKQuantityTypeIdentifierAppleExerciseTime"),
    "physical_effort": ("Physical Effort", "HKQuantityTypeIdentifierPhysicalEffort"),
    "flights_climbed": ("Flights Climbed", "HKQuantityTypeIdentifierFlightsClimbed"),
    "average_met": ("Average Metabolic Equivalent", None),
    "distance_walking_running": ("Walking + Running Distance", "HKQuantityTypeIdentifierDistanceWalkingRunning"),
    "distance_cycling": ("Cycling Distance", "HKQuantityTypeIdentifierDistanceCycling"),
    "distance_swimming": ("Swimming Distance", "HKQuantityTypeIdentifierDistanceSwimming"),
    "distance_downhill_snow_sports": ("Downhill Snow Sports Distance", "HKQuantityTypeIdentifierDistanceDownhillSnowSports"),
    "distance_other": ("Other Distance", None),
    "walking_step_length": ("Walking Step Length", "HKQuantityTypeIdentifierWalkingStepLength"),
    "walking_speed": ("Walking Speed", "HKQuantityTypeIdentifierWalkingSpeed"),
    "walking_double_support_percentage": ("Walking Double Support Percentage", "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage"),
    "walking_asymmetry_percentage": ("Walking Asymmetry Percentage", "HKQuantityTypeIdentifierWalkingAsymmetryPercentage"),
    "walking_steadiness": ("Walking Steadiness", "HKQuantityTypeIdentifierAppleWalkingSteadiness"),
    "stair_descent_speed": ("Stair Descent Speed", "HKQuantityTypeIdentifierStairDescentSpeed"),
    "stair_ascent_speed": ("Stair Ascent Speed", "HKQuantityTypeIdentifierStairAscentSpeed"),
    "running_power": ("Running Power", "HKQuantityTypeIdentifierRunningPower"),
    "running_speed": ("Running Speed", "HKQuantityTypeIdentifierRunningSpeed"),
    "running_vertical_oscillation": ("Running Vertical Oscillation", "HKQuantityTypeIdentifierRunningVerticalOscillation"),
    "running_ground_contact_time": ("Running Ground Contact Time", "HKQuantityTypeIdentifierRunningGroundContactTime"),
    "running_stride_length": ("Running Stride Length", "HKQuantityTypeIdentifierRunningStrideLength"),
    "swimming_stroke_count": ("Swimming Stroke Count", "HKQuantityTypeIdentifierSwimmingStrokeCount"),
    "underwater_depth": ("Underwater Depth", "HKQuantityTypeIdentifierUnderwaterDepth"),
    "cadence": ("Cadence", None),
    "power": ("Power Output", None),
    "speed": ("Speed", None),
    "workout_effort_score": ("Workout Effort Score", "HKQuantityTypeIdentifierWorkoutEffortScore"),
    "estimated_workout_effort_score": ("Estimated Workout Effort Score", "HKQuantityTypeIdentifierEstimatedWorkoutEffortScore"),
    # Environmental
    "environmental_audio_exposure": ("Environmental Audio Exposure", "HKQuantityTypeIdentifierEnvironmentalAudioExposure"),
    "headphone_audio_exposure": ("Headphone Audio Exposure", "HKQuantityTypeIdentifierHeadphoneAudioExposure"),
    "uv_exposure": ("UV Exposure", "HKQuantityTypeIdentifierUVExposure"),
    "inhaler_usage": ("Inhaler Usage", "HKQuantityTypeIdentifierInhalerUsage"),
    "weather_temperature": ("Weather Temperature", None),
    "weather_humidity": ("Weather Humidity", None),
    # Provider-specific
    "garmin_stress_level": ("Garmin Stress Level", None),
    "garmin_skin_temperature": ("Garmin Skin Temperature", None),
    "garmin_fitness_age": ("Garmin Fitness Age", None),
    "garmin_body_battery": ("Garmin Body Battery", None),
    # Other
    "electrodermal_activity": ("Electrodermal Activity", "HKQuantityTypeIdentifierElectrodermalActivity"),
    "push_count": ("Push Count", "HKQuantityTypeIdentifierPushCount"),
    "atrial_fibrillation_burden": ("Atrial Fibrillation Burden", "HKQuantityTypeIdentifierAtrialFibrillationBurden"),
    "insulin_delivery": ("Insulin Delivery", "HKQuantityTypeIdentifierInsulinDelivery"),
    "number_of_times_fallen": ("Number of Times Fallen", "HKQuantityTypeIdentifierNumberOfTimesFallen"),
    "number_of_alcoholic_beverages": ("Number of Alcoholic Beverages", "HKQuantityTypeIdentifierNumberOfAlcoholicBeverages"),
    "nike_fuel": ("Nike Fuel", "HKQuantityTypeIdentifierNikeFuel"),
    "hydration": ("Hydration", "HKQuantityTypeIdentifierDietaryWater"),
    # Requested but not in types array (handled via TYPE_META / special cases)
    "sleep_analysis": ("Sleep Analysis", "HKCategoryTypeIdentifierSleepAnalysis"),
    "workout": ("Workout", "HKWorkoutType"),
}

# type_code -> {"unit", "aggregation", "category"} for all 80 fixture types
TYPE_META = {
    "heart_rate": {"unit": "bpm", "aggregation": "min_max_average", "category": "heart"},
    "resting_heart_rate": {"unit": "bpm", "aggregation": "min_max_average", "category": "heart"},
    "heart_rate_variability_sdnn": {"unit": "ms", "aggregation": "min_max_average", "category": "heart"},
    "heart_rate_variability_rmssd": {"unit": "ms", "aggregation": "min_max_average", "category": "heart"},
    "heart_rate_recovery_one_minute": {"unit": "count/min", "aggregation": "min_max_average", "category": "heart"},
    "walking_heart_rate_average": {"unit": "count/min", "aggregation": "min_max_average", "category": "heart"},
    "recovery_score": {"unit": "score", "aggregation": "latest", "category": "heart"},
    "oxygen_saturation": {"unit": "%", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "blood_glucose": {"unit": "mg/dL", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "blood_pressure_systolic": {"unit": "mmHg", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "blood_pressure_diastolic": {"unit": "mmHg", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "respiratory_rate": {"unit": "brpm", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "sleeping_breathing_disturbances": {"unit": "count", "aggregation": "sum", "category": "blood_respiratory"},
    "blood_alcohol_content": {"unit": "%", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "peripheral_perfusion_index": {"unit": "%", "aggregation": "min_max_average", "category": "blood_respiratory"},
    "forced_vital_capacity": {"unit": "liters", "aggregation": "latest", "category": "blood_respiratory"},
    "forced_expiratory_volume_1": {"unit": "liters", "aggregation": "latest", "category": "blood_respiratory"},
    "peak_expiratory_flow_rate": {"unit": "L/min", "aggregation": "latest", "category": "blood_respiratory"},
    "height": {"unit": "cm", "aggregation": "latest", "category": "body"},
    "weight": {"unit": "kg", "aggregation": "latest", "category": "body"},
    "body_fat_percentage": {"unit": "%", "aggregation": "latest", "category": "body"},
    "body_mass_index": {"unit": "kg/m\u00b2", "aggregation": "latest", "category": "body"},
    "lean_body_mass": {"unit": "kg", "aggregation": "latest", "category": "body"},
    "body_temperature": {"unit": "\u00b0C", "aggregation": "min_max_average", "category": "body"},
    "skin_temperature": {"unit": "\u00b0C", "aggregation": "min_max_average", "category": "body"},
    "waist_circumference": {"unit": "cm", "aggregation": "latest", "category": "body"},
    "body_fat_mass": {"unit": "kg", "aggregation": "latest", "category": "body"},
    "skeletal_muscle_mass": {"unit": "kg", "aggregation": "latest", "category": "body"},
    "vo2_max": {"unit": "mL/kg/min", "aggregation": "latest", "category": "fitness"},
    "six_minute_walk_test_distance": {"unit": "m", "aggregation": "latest", "category": "fitness"},
    "steps": {"unit": "count", "aggregation": "sum", "category": "activity"},
    "energy": {"unit": "kcal", "aggregation": "sum", "category": "activity"},
    "basal_energy": {"unit": "kcal", "aggregation": "sum", "category": "activity"},
    "stand_time": {"unit": "minutes", "aggregation": "sum", "category": "activity"},
    "exercise_time": {"unit": "minutes", "aggregation": "sum", "category": "activity"},
    "physical_effort": {"unit": "kcal/kg/hr", "aggregation": "min_max_average", "category": "activity"},
    "flights_climbed": {"unit": "count", "aggregation": "sum", "category": "activity"},
    "average_met": {"unit": "MET", "aggregation": "min_max_average", "category": "activity"},
    "distance_walking_running": {"unit": "m", "aggregation": "sum", "category": "activity"},
    "distance_cycling": {"unit": "m", "aggregation": "sum", "category": "activity"},
    "distance_swimming": {"unit": "m", "aggregation": "sum", "category": "activity"},
    "distance_downhill_snow_sports": {"unit": "m", "aggregation": "sum", "category": "activity"},
    "distance_other": {"unit": "meters", "aggregation": "sum", "category": "activity"},
    "walking_step_length": {"unit": "cm", "aggregation": "min_max_average", "category": "activity"},
    "walking_speed": {"unit": "m/s", "aggregation": "min_max_average", "category": "activity"},
    "walking_double_support_percentage": {"unit": "%", "aggregation": "min_max_average", "category": "activity"},
    "walking_asymmetry_percentage": {"unit": "%", "aggregation": "min_max_average", "category": "activity"},
    "walking_steadiness": {"unit": "%", "aggregation": "min_max_average", "category": "activity"},
    "stair_descent_speed": {"unit": "m/s", "aggregation": "min_max_average", "category": "activity"},
    "stair_ascent_speed": {"unit": "m/s", "aggregation": "min_max_average", "category": "activity"},
    "running_power": {"unit": "watts", "aggregation": "min_max_average", "category": "activity"},
    "running_speed": {"unit": "m/s", "aggregation": "min_max_average", "category": "activity"},
    "running_vertical_oscillation": {"unit": "cm", "aggregation": "min_max_average", "category": "activity"},
    "running_ground_contact_time": {"unit": "ms", "aggregation": "min_max_average", "category": "activity"},
    "running_stride_length": {"unit": "cm", "aggregation": "min_max_average", "category": "activity"},
    "swimming_stroke_count": {"unit": "count", "aggregation": "sum", "category": "activity"},
    "underwater_depth": {"unit": "m", "aggregation": "min_max_average", "category": "activity"},
    "cadence": {"unit": "rpm", "aggregation": "min_max_average", "category": "activity"},
    "power": {"unit": "watts", "aggregation": "min_max_average", "category": "activity"},
    "speed": {"unit": "m/s", "aggregation": "min_max_average", "category": "activity"},
    "workout_effort_score": {"unit": "apple_effort_score", "aggregation": "latest", "category": "activity"},
    "estimated_workout_effort_score": {"unit": "apple_effort_score", "aggregation": "latest", "category": "activity"},
    "environmental_audio_exposure": {"unit": "dBASPL", "aggregation": "min_max_average", "category": "environmental"},
    "headphone_audio_exposure": {"unit": "dBASPL", "aggregation": "min_max_average", "category": "environmental"},
    "uv_exposure": {"unit": "count", "aggregation": "sum", "category": "environmental"},
    "inhaler_usage": {"unit": "count", "aggregation": "sum", "category": "environmental"},
    "weather_temperature": {"unit": "\u00b0C", "aggregation": "min_max_average", "category": "environmental"},
    "weather_humidity": {"unit": "%", "aggregation": "min_max_average", "category": "environmental"},
    "garmin_stress_level": {"unit": "score", "aggregation": "min_max_average", "category": "provider_specific"},
    "garmin_skin_temperature": {"unit": "\u00b0C", "aggregation": "min_max_average", "category": "provider_specific"},
    "garmin_fitness_age": {"unit": "years", "aggregation": "latest", "category": "provider_specific"},
    "garmin_body_battery": {"unit": "%", "aggregation": "min_max_average", "category": "provider_specific"},
    "electrodermal_activity": {"unit": "S", "aggregation": "min_max_average", "category": "other"},
    "push_count": {"unit": "count", "aggregation": "sum", "category": "other"},
    "atrial_fibrillation_burden": {"unit": "%", "aggregation": "min_max_average", "category": "other"},
    "insulin_delivery": {"unit": "IU", "aggregation": "sum", "category": "other"},
    "number_of_times_fallen": {"unit": "count", "aggregation": "sum", "category": "other"},
    "number_of_alcoholic_beverages": {"unit": "count", "aggregation": "sum", "category": "other"},
    "nike_fuel": {"unit": "count", "aggregation": "sum", "category": "other"},
    "hydration": {"unit": "mL", "aggregation": "sum", "category": "other"},
}

# type_codes that are not standard HealthKit quantity types.
NON_HK_TYPES = {tc for tc, (_name, hk) in TYPE_MAP.items() if hk is None}


def metric_name(type_code: str) -> str:
    """Return the human-readable metric display name for a bridge type_code."""
    return TYPE_MAP.get(type_code, (type_code, None))[0]


def healthkit_identifier(type_code: str):
    """Return the canonical HealthKit identifier or None."""
    return TYPE_MAP.get(type_code, (None, None))[1]


def describe_type(type_code: str) -> dict:
    """Return a dict describing a bridge type_code.

    For known types (present in both TYPE_MAP and TYPE_META):
        {"type_code", "name", "healthkit", "unit", "aggregation", "category", "known": True}
    For unknown codes:
        {"type_code": code, "name": code, "healthkit": None, "unit": None,
         "category": "other", "aggregation": "min_max_average", "known": False}
    """
    if type_code in TYPE_MAP and type_code in TYPE_META:
        name, hk = TYPE_MAP[type_code]
        meta = TYPE_META[type_code]
        return {
            "type_code": type_code,
            "name": name,
            "healthkit": hk,
            "unit": meta["unit"],
            "aggregation": meta["aggregation"],
            "category": meta["category"],
            "known": True,
        }
    return {
        "type_code": type_code,
        "name": type_code,
        "healthkit": None,
        "unit": None,
        "category": "other",
        "aggregation": "min_max_average",
        "known": False,
    }


def unmapped_types(type_codes: list) -> list:
    """Return sorted unique type_codes not present in TYPE_MAP."""
    return sorted(set(tc for tc in type_codes if tc not in TYPE_MAP))
