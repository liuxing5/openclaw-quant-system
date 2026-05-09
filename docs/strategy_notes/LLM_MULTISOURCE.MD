-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.concept_membership (
  ts_code character varying NOT NULL,
  concept_code character varying NOT NULL,
  concept_name character varying,
  update_date date,
  CONSTRAINT concept_membership_pkey PRIMARY KEY (ts_code, concept_code)
);
CREATE TABLE public.daily_candidates (
  id bigint NOT NULL DEFAULT nextval('daily_candidates_id_seq'::regclass),
  snapshot_date date NOT NULL,
  ts_code character varying NOT NULL,
  stock_name character varying,
  mention_count integer,
  source_diversity integer,
  consensus_score double precision,
  llm_score double precision,
  quant_score double precision,
  final_score double precision,
  logic_tags ARRAY,
  selected boolean DEFAULT false,
  position_pct double precision,
  entry_low numeric,
  entry_high numeric,
  stop_loss numeric,
  target_1 numeric,
  target_2 numeric,
  sources jsonb,
  created_at timestamp with time zone DEFAULT now(),
  run_mode character varying DEFAULT 'afternoon'::character varying,
  source character varying NOT NULL DEFAULT 'llm_multisource'::character varying,
  CONSTRAINT daily_candidates_pkey PRIMARY KEY (id)
);
CREATE TABLE public.daily_quotes (
  ts_code character varying NOT NULL,
  trade_date date NOT NULL,
  open numeric,
  high numeric,
  low numeric,
  close numeric,
  volume bigint,
  amount numeric,
  pct_chg double precision,
  turnover_rate double precision,
  CONSTRAINT daily_quotes_pkey PRIMARY KEY (ts_code, trade_date)
);
CREATE TABLE public.extracted_recommendations (
  id bigint NOT NULL DEFAULT nextval('extracted_recommendations_id_seq'::regclass),
  raw_signal_id bigint,
  source_name character varying,
  ts_code character varying NOT NULL,
  stock_name character varying,
  recommendation_type character varying,
  strength integer,
  logic_category character varying,
  logic_summary text,
  target_price numeric,
  stop_loss numeric,
  time_horizon character varying,
  raw_excerpt text,
  confidence double precision,
  pub_time timestamp with time zone,
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT extracted_recommendations_pkey PRIMARY KEY (id),
  CONSTRAINT extracted_recommendations_raw_signal_id_fkey FOREIGN KEY (raw_signal_id) REFERENCES public.raw_signals(id)
);
CREATE TABLE public.feed_sources (
  id integer NOT NULL DEFAULT nextval('feed_sources_id_seq'::regclass),
  name character varying NOT NULL UNIQUE,
  route text NOT NULL,
  category character varying NOT NULL,
  tier integer NOT NULL DEFAULT 2,
  weight double precision NOT NULL DEFAULT 1.0,
  poll_interval_sec integer DEFAULT 600,
  enabled boolean DEFAULT true,
  last_success_at timestamp with time zone,
  last_error_at timestamp with time zone,
  last_error_msg text,
  consecutive_failures integer DEFAULT 0,
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT feed_sources_pkey PRIMARY KEY (id)
);
CREATE TABLE public.hsgt_individual (
  ts_code character varying NOT NULL,
  trade_date date NOT NULL,
  hold_shares bigint,
  hold_market_cap numeric,
  net_buy_amount numeric,
  CONSTRAINT hsgt_individual_pkey PRIMARY KEY (ts_code, trade_date)
);
CREATE TABLE public.lhb_detail (
  id bigint NOT NULL DEFAULT nextval('lhb_detail_id_seq'::regclass),
  trade_date date,
  ts_code character varying,
  stock_name character varying,
  reason text,
  buy_amt numeric,
  sell_amt numeric,
  net_amt numeric,
  is_inst boolean,
  CONSTRAINT lhb_detail_pkey PRIMARY KEY (id)
);
CREATE TABLE public.performance_tracking (
  id bigint NOT NULL DEFAULT nextval('performance_tracking_id_seq'::regclass),
  candidate_id bigint,
  ts_code character varying,
  rec_date date,
  entry_price numeric,
  t1_high numeric,
  t1_low numeric,
  t1_close numeric,
  t1_return double precision,
  t5_close numeric,
  t5_return double precision,
  t20_close numeric,
  t20_return double precision,
  hit_target boolean,
  hit_stop boolean,
  updated_at timestamp with time zone DEFAULT now(),
  t1_hit_target boolean,
  t1_hit_stop boolean,
  t5_hit_target boolean,
  t5_hit_stop boolean,
  t20_hit_target boolean,
  t20_hit_stop boolean,
  CONSTRAINT performance_tracking_pkey PRIMARY KEY (id),
  CONSTRAINT performance_tracking_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.daily_candidates(id)
);
CREATE TABLE public.push_history (
  id bigint NOT NULL DEFAULT nextval('push_history_id_seq'::regclass),
  candidate_id bigint,
  push_type character varying,
  chat_id character varying,
  message_id bigint,
  pushed_at timestamp with time zone DEFAULT now(),
  user_action character varying,
  user_action_at timestamp with time zone,
  CONSTRAINT push_history_pkey PRIMARY KEY (id),
  CONSTRAINT push_history_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.daily_candidates(id)
);
CREATE TABLE public.raw_signals (
  id bigint NOT NULL DEFAULT nextval('raw_signals_id_seq'::regclass),
  source_id integer,
  source_name character varying,
  source_tier integer,
  title text,
  content text,
  url text,
  pub_time timestamp with time zone,
  fetch_time timestamp with time zone DEFAULT now(),
  content_hash character varying UNIQUE,
  CONSTRAINT raw_signals_pkey PRIMARY KEY (id)
);
CREATE TABLE public.source_performance (
  id bigint NOT NULL DEFAULT nextval('source_performance_id_seq'::regclass),
  source_id integer,
  eval_date date NOT NULL,
  rolling_window_days integer DEFAULT 60,
  rec_count integer,
  avg_t1_return double precision,
  avg_t5_return double precision,
  win_rate double precision,
  ic_value double precision,
  CONSTRAINT source_performance_pkey PRIMARY KEY (id),
  CONSTRAINT source_performance_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id)
);
CREATE TABLE public.stock_basic_info (
  ts_code character varying NOT NULL,
  stock_name character varying NOT NULL,
  market character varying,
  list_date date,
  is_st boolean DEFAULT false,
  is_active boolean DEFAULT true,
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT stock_basic_info_pkey PRIMARY KEY (ts_code)
);