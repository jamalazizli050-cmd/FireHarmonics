// Types for the FireHarmonics FastAPI backend (schema_version 2.0).
// Source of truth: workspace/backend/FireHarmonics/FRONTEND_INTEGRATION.md,
// FireDetectionAPP/schemas.py, main.py, jobs.py, satellites.py and analytics.py.
// Every field is optional/nullable where the backend may omit it; the UI never fills gaps with invented values.

export const FIRMS_SOURCES = [
  'VIIRS_NOAA20_NRT',
  'VIIRS_NOAA20_SP',
  'VIIRS_NOAA21_NRT',
  'VIIRS_NOAA21_SP',
  'VIIRS_SNPP_NRT',
  'VIIRS_SNPP_SP',
  'MODIS_NRT',
  'MODIS_SP',
  'LANDSAT_NRT',
] as const;
export type FirmsSource = (typeof FIRMS_SOURCES)[number];

export type MlMode = 'download' | 'cache_only' | 'off';

export interface AnalysisRequest {
  latitude: number;
  longitude: number;
  radius_km: number;
  location_name?: string | null;
  source?: FirmsSource;
  start_date?: string;
  end_date?: string;
  lookback_days?: number;
  ml_mode?: MlMode;
  target_month?: string | null;
  history_years?: number;
}

export type JobStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed';
export type ResultStatus = 'complete' | 'partial' | 'descriptive_only';

export interface JobError {
  code?: string | null;
  detail: string;
  recoverable?: boolean;
  status_code?: number;
}

export interface JobLinks {
  status: string;
  events: string;
  result: string;
  retry: string;
  export: string;
}

export interface JobResponse {
  status_code: number;
  job_id: string;
  status: JobStatus;
  progress: number;
  stage: string;
  eta_seconds: number | null;
  nasa_pause_seconds: number | null;
  cache_hit: boolean;
  attempt: number;
  error: JobError | null;
  message: string | null;
  result_status: ResultStatus | null;
  links: JobLinks;
  request?: Partial<AnalysisRequest>;
  /** Only in the POST /api/v1/analyses response: proves this client owns the job (needed to cancel/retry). */
  owner_token?: string | null;
}

export interface Period {
  start_date: string | null;
  end_date: string | null;
}

export interface Provenance {
  provider: string;
  product: FirmsSource | string;
  instrument: string;
  platforms: string[];
  processing: string;
  nominal_resolution_m: number;
  product_kind: string;
  provider_details?: { name: string; role: string; url: string };
  requested_period?: Period;
  observed_period?: Period;
}

export interface Coverage {
  start_date: string | null;
  end_date: string | null;
  complete: boolean;
  requested_start_date?: string | null;
  requested_end_date?: string | null;
  source_kind?: string;
  downloaded_at?: string | null;
  request_count?: number;
  raw_row_count?: number;
  unique_rows_before_radius_filter?: number;
}

export interface DailyCount {
  date: string;
  detections: number | null;
}
export interface MonthlyCount {
  month: string;
  detections: number | null;
  complete: boolean;
}
export interface AnnualCount {
  year: number;
  detections: number | null;
  complete: boolean;
}

export interface DescriptiveHistory {
  mean_annual_detections: number | null;
  complete_years: number[];
  daily_counts: DailyCount[];
  monthly_counts: MonthlyCount[];
  annual_counts: AnnualCount[];
  explanation?: string;
}

export interface DescriptiveSeasonal {
  status: string;
  peak_month: number | null;
  monthly_mean_detections: { month: number; detections: number }[];
  explanation?: string;
}

export interface DescriptiveTrend {
  status: string;
  slope_detections_per_year: number | null;
  direction: 'increasing' | 'decreasing' | 'flat' | null;
  complete_years?: number[];
  method?: string;
  explanation?: string;
}

export interface DescriptiveAnomalies {
  status: string;
  items: { month: string; detections: number; complete: boolean; z_score: number }[];
  method?: string;
  explanation?: string;
}

export interface DescriptiveHotspot {
  latitude: number;
  longitude: number;
  detections: number;
  max_frp_mw: number | null;
  first_date: string;
  last_date: string;
  cell_size_degrees?: number;
}

export interface ModelComparisonEntry {
  probability: number | null;
  used: boolean;
  validated_for_this_location_and_lead?: boolean;
  variant?: string;
  note?: string;
}

export interface Prediction {
  status?: string;
  probability: number | null;
  method: 'xgboost_pooled' | 'seasonal_baseline' | 'unavailable';
  target_month?: string | null;
  explanation?: string | null;
  error?: JobError | null;
  target_definition?: string | null;
  relative_activity?: {
    ratio_to_seasonal_norm: number | null;
    category: string | null;
    definition?: string;
    recent_observed_months?: { month: string; detections: number | null; same_month_median_other_years: number | null }[];
  } | null;
  last_observed_month?: string | null;
  lead_months?: number | null;
  method_reason?: string | null;
  baseline_variant?: string | null;
  seasonal_norm?: {
    calendar_month?: string;
    years_observed?: number;
    years_with_detections?: number;
    frequency?: number | null;
    median_detections?: number | null;
    mean_detections?: number | null;
  } | null;
  validation_evidence?: {
    setting?: string;
    lead_evaluated?: number;
    method_metrics_heldout?: Record<string, number | null>;
    xgboost_vs_best_baseline_brier_skill?: { bss: number | null; ci95?: (number | null)[]; n_months?: number };
    [key: string]: unknown;
  } | null;
  uncertainty?: {
    empirical_reliability?: {
      forecast_bin?: number[];
      n_heldout_forecasts?: number;
      observed_frequency_heldout?: number | null;
      mean_forecast_heldout?: number | null;
    } | null;
    note?: string;
  } | null;
  model_comparison?: {
    xgboost_pooled?: ModelComparisonEntry | null;
    seasonal_baseline?: ModelComparisonEntry | null;
  } | null;
  model?: { name?: string; design?: string; trained_regions?: string[]; training_period?: string[] } | null;
  reason?: string | null;
  observed_rate_upper_bound_95?: number | null;
}

export interface MlMonthly {
  month: string;
  detections: number | null;
  frp_sum_mw: number | null;
  observed: boolean;
  coverage_fraction: number | null;
  products: string | null;
}

export interface MlCalendarMonth {
  month: string;
  years_observed: number;
  years_with_detections: number;
  fraction_of_years_with_detections: number | null;
  median_detections: number | null;
  mean_detections: number | null;
}

export interface MlAnomaly {
  month: string;
  detections: number | null;
  same_month_median_other_years: number | null;
  robust_z: number | null;
  direction: string | null;
  method?: string;
}

export interface MlHotspot {
  cell_lat: number;
  cell_lon: number;
  cell_size_deg: number;
  total_detections: number;
  mean_annual_detections?: number | null;
  share_of_disc_detections?: number | null;
  years_active?: number;
  years_observed?: number;
  recurring: boolean;
  gi_star_z: number | null;
  gi_star_hotspot_99: boolean;
}

export interface CalendarMonth {
  month: string;
  value: number | null;
  source: 'modis_aqua' | 'viirs_snpp_scaled' | 'viirs_noaa20_scaled' | null;
  /** 90 % interval: link-coefficient bootstrap + counting noise. Null for Aqua months (the reference itself). */
  interval_90: [number, number] | null;
  /** Active 1-km cell-overpasses seen by each sensor (unscaled). */
  modis_aqua: number | null;
  viirs_snpp: number | null;
  viirs_noaa20: number | null;
  /** Against the same calendar month of the fixed baseline years. */
  percentile: number | null;
  baseline_median: number | null;
  diff_from_median: number | null;
  anomaly: 'high' | null;
}

export interface SeasonTiming {
  status: 'ok' | 'insufficient_data' | 'no_activity' | string;
  explanation?: string;
  fire_year_starts?: string;
  definition?: string;
  typical?: { onset: string; peak: string; end: string; length_months: number };
  years?: { fire_year: string; onset: string; peak: string; end: string; length_months: number; detections: number }[];
  current?: {
    fire_year_start: string;
    months_elapsed: number;
    detections_so_far: number;
    typical_detections_by_now: number;
    onset: string | null;
    onset_status: 'earlier_than_usual' | 'later_than_usual' | 'as_usual' | 'not_started_later_than_usual' | null;
  };
}

export type StratumValues<T> = { D: T; N: T };

/** One link of the continuity chain (reference <- target), per day/night stratum. */
export interface ChainLink {
  link: 'aqua_snpp' | 'snpp_n20' | string;
  reference: string;
  target: string;
  status?: 'unavailable' | string;
  explanation?: string;
  fit_years?: number[];
  ratio?: StratumValues<number>;
  ratio_ci90?: StratumValues<[number, number]>;
  prior: StratumValues<number>;
  prior_scope: StratumValues<string>;
  local_weight?: StratumValues<number>;
  overlap_units?: { reference: StratumValues<number>; target: StratumValues<number> };
  dispersion_k?: number | null;
}

export interface MethodScore {
  median_abs_pct_error_annual: number | null;
  median_abs_log_error_monthly: number | null;
  anomaly_rank_correlation: number | null;
  years_scored: number[];
}

/** Held-out-in-time test of one link: fitted on train_years, scored on test_years. */
export interface ForwardTest {
  status: 'ok' | 'insufficient_years' | 'too_little_activity' | 'unavailable' | string;
  explanation?: string;
  train_years?: number[];
  test_years?: number[];
  months_tested?: number;
  methods?: { continuity_chain: MethodScore | null; pixel_ratio: MethodScore | null; raw_target: MethodScore | null };
  interval_90_coverage?: number;
  beats_pixel_ratio?: boolean | null;
  by_year?: {
    year: number;
    reference: number;
    continuity_chain: number;
    reference_pixels: number;
    pixel_ratio: number | null;
    raw_target_pixels: number;
  }[];
}

/** MODIS+VIIRS calendar on the Aqua-MODIS scale (FireLens `unified_calendar`, continuity chain). */
export interface UnifiedCalendar {
  status: 'ok' | 'preparing' | 'unavailable' | 'failed' | 'disabled' | string;
  explanation?: string;
  label?: string;
  method?: string;
  countries?: string[];
  reference_sensor?: string;
  calibrated_sensor?: string;
  unit?: string;
  unit_definition?: string;
  kappa?: number;
  chain?: ChainLink[];
  validation?: {
    method: string;
    aqua_snpp: ForwardTest;
    snpp_n20: ForwardTest;
    seam_2012?: { harmonized_over_aqua_2012_2013: number | null; explanation: string };
  };
  baseline_years?: [number, number];
  monthly?: CalendarMonth[];
  season?: SeasonTiming;
  limitations?: string[];
}

export interface CurrentConditions {
  status: 'ok' | 'unavailable' | 'insufficient_history' | 'disabled' | 'failed' | string;
  explanation?: string;
  window?: { start: string; end: string; days: number } | null;
  sensor?: string;
  reference?: string;
  /** NOAA-20 near-real-time detections (pixels) in the window. */
  detections?: number;
  /** The same as active 1-km cell-overpasses, and on the S-NPP scale (bridge ratio applied). */
  active_cells?: number;
  snpp_equivalent?: number;
  unit?: string;
  category?:
    | 'highest_on_record'
    | 'well_above_normal'
    | 'above_normal'
    | 'near_normal'
    | 'below_normal'
    | 'quiet_as_usual'
    | 'isolated_detections'
    | string;
  percentile?: number;
  history_median?: number;
  history_p90?: number;
  history_max?: number;
  history?: { year: number; detections: number }[];
  limitations?: string[];
}

export interface MlAnalysis {
  status: 'ok' | 'partial' | 'insufficient_data' | 'failed' | 'unavailable' | 'not_run' | string;
  mode?: string;
  error?: JobError | null;
  explanation?: string | null;
  engine?: Record<string, unknown>;
  series?: { provider?: string; instrument?: string; platform?: string; products?: string[]; resolution_m?: number; description?: string };
  historical_activity?: {
    series?: string;
    unit?: string;
    n_months_observed?: number;
    n_months_unobserved?: number;
    total_detections_observed_months?: number | null;
    months_with_detections?: number;
    share_of_observed_months_with_detections?: number | null;
    complete_years?: number[];
    annual_totals_complete_years?: Record<string, number>;
    mean_annual_detections?: number | null;
    median_annual_detections?: number | null;
    detections_per_1000km2_per_year?: number | null;
    disc_area_km2?: number;
    max_month?: { month: string; detections: number } | null;
    mean_frp_per_detection_mw?: number | null;
    night_share?: number | null;
    monthly?: MlMonthly[];
  };
  seasonal_patterns?: {
    status?: string;
    by_calendar_month?: MlCalendarMonth[];
    complete_years_used?: number[];
    monthly_share_of_annual_detections?: Record<string, number | null>;
    peak_month?: string | null;
    core_season_months_80pct?: string[];
    lowest_activity_months?: string[];
    months_below_1pct_share?: string[];
    note?: string;
  };
  long_term_trends?: {
    status?: string;
    method?: string;
    theil_sen_pct_per_year?: number | null;
    ci95_pct_per_year?: (number | null)[] | null;
    mann_kendall_tau?: number | null;
    p_value?: number | null;
    'significant_at_0.05'?: boolean | null;
    direction?: string | null;
    years?: number[] | number;
    n_complete_years?: number;
    note?: string;
  };
  anomalies?: MlAnomaly[];
  anomaly_detection?: {
    status?: string;
    n_months_tested?: number;
    z_threshold?: number;
    min_other_years?: number;
    cell_month_outliers?: Record<string, unknown>[];
    cell_month_outliers_method?: Record<string, unknown>;
    method?: string;
  };
  hotspots?: MlHotspot[];
  hotspot_analysis?: Record<string, unknown> & { status?: string; method?: string };
  data_coverage?: {
    sensor_series?: string;
    window?: { start: string; end: string; months: number };
    products?: { product: string; first_day: string; last_day: string }[];
    product_availability?: Record<string, (string | null)[]>;
    months_observed?: number;
    months_unobserved?: number;
    unobserved_months?: { month: string; coverage_fraction: number | null }[];
    last_observed_month?: string | null;
    zero_vs_missing?: string;
    retrieval?: Record<string, unknown>;
    complete?: boolean;
    attribution?: string;
  };
  harmonization?: {
    series_used?: string;
    modis_viirs_mixed?: boolean;
    project_region?: string | null;
    regional_harmonization_status?: string | null;
    note?: string;
  };
  unified_calendar?: UnifiedCalendar;
  current_conditions?: CurrentConditions;
  limitations?: string[];
  warnings?: string[];
}

export interface SeriesDescription {
  provider?: string;
  product?: string;
  products?: string[];
  instrument: string;
  platforms: string[];
  processing?: string;
  nominal_resolution_m?: number;
  product_kind?: string;
  used?: boolean;
}

export interface SatelliteView {
  status: 'this_result' | 'separate_request' | string;
  source: string;
  detection_count: number | null;
  explanation?: string;
}

export interface HarmonizedView {
  status: 'regional_only' | 'unavailable' | string;
  region_id: string | null;
  location_scale: string;
  validation: { overall?: string | null; count?: string | null; frp?: string | null } | null;
  series_url: string | null;
  explanation?: string;
}

/** Relative activity index (backend `satellite_analysis.views.normalized`): not harmonization, not validated. */
export interface NormalizedSeries {
  series: 'ml_record' | 'descriptive' | string;
  product: string;
  instrument: string;
  status: 'available' | 'no_activity' | string;
  reference_period: { start: string; end: string; months: number };
  reference_mean_monthly_detections: number;
  monthly: { month: string; detections: number | null; index: number | null }[];
  explanation?: string;
}

export interface NormalizedView {
  status: 'available' | 'insufficient_data' | string;
  method: string;
  validated: boolean;
  label: string;
  definition: string;
  min_months: number;
  series: NormalizedSeries[];
  limitations: string[];
  explanation: string | null;
}

export interface SatelliteAnalysis {
  provider: { name: string; role: string; url?: string };
  descriptive_series: SeriesDescription;
  forecast_series: SeriesDescription;
  views: { raw_modis: SatelliteView; raw_viirs: SatelliteView; harmonized: HarmonizedView; normalized?: NormalizedView };
  modis_viirs_mixed: boolean;
  warnings: string[];
}

export interface Overview {
  location: { name: string | null; latitude: number; longitude: number; radius_km: number };
  status: ResultStatus | null;
  descriptive: {
    source: string;
    instrument: string;
    period: Period;
    detection_count: number | null;
    mean_annual_detections: number | null;
    peak_month: number | null;
  };
  ml: {
    status: string;
    series?: string;
    period?: { start: string; end: string; months: number } | null;
    total_detections_observed_months?: number | null;
    mean_annual_detections?: number | null;
    peak_month?: string | null;
    trend_direction?: string | null;
    trend_pct_per_year?: number | null;
    trend_significant?: boolean | null;
    anomaly_count?: number | null;
    gi_star_hotspot_cells?: number | null;
  };
  forecast: {
    status?: string | null;
    probability: number | null;
    method: string;
    target_month?: string | null;
    relative_activity?: string | null;
  };
  harmonization_status: string | null;
}

export interface Components {
  descriptive: string;
  ml_analysis: string;
  forecast: string;
  harmonization: string;
}

export interface AnalysisResult {
  schema_version: string;
  status: ResultStatus | null;
  location: {
    latitude: number;
    longitude: number;
    radius_km: number;
    name: string | null;
    crs?: string;
    distance_method?: string;
  };
  source: string;
  provenance: Provenance;
  coverage: Coverage;
  detection_count: number | null;
  historical_activity: DescriptiveHistory;
  seasonal_patterns: DescriptiveSeasonal;
  long_term_trends: DescriptiveTrend;
  anomalies: DescriptiveAnomalies;
  hotspots: DescriptiveHotspot[];
  prediction: Prediction;
  ml_analysis: MlAnalysis;
  satellite_analysis: SatelliteAnalysis | null;
  overview: Overview | null;
  components: Components | null;
  warnings: string[];
}

export interface ResultEnvelope {
  status_code: number;
  job_id: string;
  status: 'completed';
  result: AnalysisResult;
}

export interface ModelStatus {
  status_code: number;
  status: string;
  available: boolean;
  engine?: Record<string, unknown>;
  models?: {
    id: string;
    role: string;
    method?: string;
    loaded?: boolean | null;
    selected_by_validation_gate?: boolean | null;
    note?: string | null;
    description?: string;
  }[];
  prediction?: { definition?: string; explanation?: string };
}

export interface SourcesResponse {
  status_code: number;
  default: FirmsSource;
  sources: FirmsSource[];
  notes?: string[];
}

export interface HarmonizationRegion {
  region_id: string;
  description: string | null;
  bbox: { west: number; south: number; east: number; north: number } | null;
  status: string | null;
  count_status: string | null;
  frp_status: string | null;
  notes: string | null;
  series_url: string;
}

export interface HarmonizationOverview {
  status_code: number;
  available: boolean;
  status: string;
  product?: string;
  scale?: string;
  domain?: string;
  regions: HarmonizationRegion[];
  limitations: string[];
}

export interface HarmonizedPoint {
  month: string;
  viirs_raw?: { count: number | null; frp_mw: number | null } | null;
  modis_equivalent?: {
    count: number | null;
    count_interval_90?: (number | null)[] | null;
    frp_mw?: number | null;
    frp_interval_90?: (number | null)[] | null;
  } | null;
  modis_aqua_observed?: { count: number | null; frp_mw: number | null } | null;
}

export interface HarmonizedSeries {
  status_code: number;
  status: string;
  location_scale: string;
  region_id: string;
  domain?: string;
  prediction_type?: string;
  validation?: {
    count?: { status?: string; [key: string]: unknown };
    frp?: { status?: string; [key: string]: unknown };
  };
  interval_method?: string;
  data: HarmonizedPoint[];
  [key: string]: unknown;
}

export const CSV_TABLES = [
  'daily',
  'monthly',
  'annual',
  'hotspots',
  'ml_monthly',
  'ml_seasonal',
  'ml_anomalies',
  'ml_hotspots',
  'calendar',
] as const;
export type CsvTable = (typeof CSV_TABLES)[number];
