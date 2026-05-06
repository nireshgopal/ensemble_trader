-- =============================================================================
-- E1 Shadow Mode Simulation Schema
-- Mirrors all production sandbox.e1_* tables in an isolated namespace.
-- These tables are ONLY written to during shadow/backtest runs.
-- Production tables (sandbox.e1_*) are NEVER touched by the shadow runner.
-- =============================================================================

-- 1. Positions (mirrors sandbox.e1_positions exactly + sim_run_id for grouping)
CREATE SEQUENCE IF NOT EXISTS sim_pos_seq;
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_positions (
    id                              BIGINT DEFAULT nextval('sim_pos_seq'),
    ticker                          VARCHAR,
    status                          VARCHAR,
    entry_date                      DATE,
    entry_price                     DOUBLE,
    shares                          INTEGER,
    dollar_value                    DOUBLE,
    ensemble_score                  DOUBLE,
    entry_regime                    VARCHAR,
    dominant_cluster                VARCHAR,
    stop_loss                       DOUBLE,
    target_1_hit                    BOOLEAN DEFAULT FALSE,
    score_scalar                    DOUBLE,
    vote_signal_1                   DOUBLE,
    vote_signal_2                   DOUBLE,
    vote_signal_3                   DOUBLE,
    vote_signal_4                   DOUBLE,
    vote_signal_5                   DOUBLE,
    vote_signal_6                   DOUBLE,
    vote_signal_7                   DOUBLE,
    exit_date                       DATE,
    exit_price                      DOUBLE,
    exit_trigger                    VARCHAR,
    exit_regime                     VARCHAR,
    pnl_pct                         DOUBLE,
    pnl_dollars                     DOUBLE,
    days_held                       INTEGER,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    target_1                        DOUBLE,
    target_2                        DOUBLE,
    weights_version                 VARCHAR,
    shares_total                    INTEGER,
    shares_remaining                INTEGER,
    is_fully_closed                 BOOLEAN DEFAULT FALSE,
    regime_at_entry                 VARCHAR,
    initial_stop                    DECIMAL(10,4),
    breakeven_trigger               DECIMAL(10,4),
    stop_stage                      VARCHAR DEFAULT 'INITIAL',
    highest_close_since_t1          DECIMAL(10,4),
    trailing_stop                   DECIMAL(10,4),
    trailing_mult_override          DECIMAL(4,2),
    t1_price                        DECIMAL(10,4),
    t2_price                        DECIMAL(10,4),
    t1_hit_date                     DATE,
    t2_hit_date                     DATE,
    t1_shares_sold                  INTEGER,
    t2_shares_sold                  INTEGER,
    cash_harvested                  DECIMAL(12,4),
    max_hold_days                   INTEGER,
    trend_cluster_score_3d_min      DECIMAL(6,4),
    days_since_earnings_at_entry    INTEGER,
    atr_at_entry                    DECIMAL(10,4),
    cluster_dominance_pct           DECIMAL(6,4),
    entry_score                     DECIMAL(6,4),
    is_beta_sweep                   BOOLEAN DEFAULT FALSE,
    sector_rs_at_entry              FLOAT,
    effective_sector_cap            FLOAT,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR,
    sim_date                        DATE
);

-- 2. Trade Log (mirrors sandbox.e1_trade_log exactly)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_trade_log (
    id                              INTEGER,
    position_id                     INTEGER,
    ticker                          VARCHAR,
    action                          VARCHAR,
    trade_date                      DATE,
    price                           DOUBLE,
    shares                          INTEGER,
    dollar_value                    DOUBLE,
    trigger                         VARCHAR,
    reason                          VARCHAR,
    regime                          VARCHAR,
    pnl_pct                         DOUBLE,
    pnl_dollars                     DOUBLE,
    days_held                       INTEGER,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ensemble_score                  DOUBLE,
    dominant_cluster                VARCHAR,
    stop_loss                       DOUBLE,
    score_scalar                    DOUBLE,
    vote_signal_1                   DOUBLE,
    vote_signal_2                   DOUBLE,
    vote_signal_3                   DOUBLE,
    vote_signal_4                   DOUBLE,
    vote_signal_5                   DOUBLE,
    vote_signal_6                   DOUBLE,
    vote_signal_7                   DOUBLE,
    assumed_cost_bp                 DECIMAL(6,2),
    expected_price                  DECIMAL(10,2),
    weights_version                 VARCHAR,
    target_1                        DOUBLE,
    target_2                        DOUBLE,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR,
    sim_date                        DATE
);

-- 3. Position Fills (mirrors sandbox.e1_position_fills exactly)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_position_fills (
    fill_id                         VARCHAR,
    position_id                     VARCHAR,
    ticker                          VARCHAR,
    fill_date                       DATE,
    fill_type                       VARCHAR,
    shares                          INTEGER,
    fill_price                      DECIMAL(10,4),
    dollar_value                    DECIMAL(12,2),
    stop_stage_at_fill              VARCHAR,
    spy_price_at_fill               DECIMAL(10,4),
    notes                           VARCHAR,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR,
    sim_date                        DATE
);

-- 4. Order History (mirrors sandbox.e1_order_history + richer sim fields)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_order_history (
    order_id                        VARCHAR PRIMARY KEY,
    client_order_id                 VARCHAR,
    ticker                          VARCHAR,
    side                            VARCHAR,
    qty                             INTEGER,
    status                          VARCHAR DEFAULT 'filled',
    filled_qty                      INTEGER,
    filled_avg_price                DOUBLE,
    limit_price                     DOUBLE,
    stop_price                      DOUBLE,
    order_class                     VARCHAR,   -- MARKET / LIMIT / STOP / OCO / BRACKET
    order_type                      VARCHAR,
    reject_reason                   VARCHAR,   -- populated if mock raises an error
    submitted_at                    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR,
    sim_date                        DATE
);

-- 5. Reconciler Flags (mirrors sandbox.e1_reconciler_flags exactly)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_reconciler_flags (
    flag_id                         VARCHAR,
    position_id                     VARCHAR,
    flag_date                       DATE,
    flag_type                       VARCHAR,
    db_value                        VARCHAR,
    alpaca_value                    VARCHAR,
    resolved                        BOOLEAN DEFAULT FALSE,
    resolved_at                     TIMESTAMP,
    notes                           VARCHAR,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR,
    sim_date                        DATE
);

-- 6. Sector Caps History (mirrors sandbox.e1_sector_caps_history)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_sector_caps_history (
    date                            DATE,
    regime                          VARCHAR,
    sector                          VARCHAR,
    base_cap                        FLOAT,
    sector_rs                       FLOAT,
    effective_cap                   FLOAT,
    adjustment_reason               VARCHAR,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR
);

-- 7. Beta Sweeper Log (mirrors sandbox.e1_beta_sweeper_log)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_beta_sweeper_log (
    date                            DATE,
    regime                          VARCHAR,
    portfolio_value                 FLOAT,
    cash_at_trigger                 FLOAT,
    exposure_pre                    FLOAT,
    sweep_amt                       FLOAT,
    symbol                          VARCHAR,
    action                          VARCHAR,
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Shadow-only metadata
    sim_run_id                      VARCHAR
);

-- 8. Equity Curve (Shadow Mode only — tracks daily P&L of the simulation)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_equity_curve (
    sim_run_id                      VARCHAR,
    sim_date                        DATE,
    portfolio_value                 DOUBLE,
    cash                            DOUBLE,
    invested                        DOUBLE,
    open_positions                  INTEGER,
    regime                          VARCHAR,
    data_coverage_note              VARCHAR,  -- records any neutralized signals
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sim_run_id, sim_date)
);

-- 9. Run Manifest (tracks metadata about each shadow run)
CREATE TABLE IF NOT EXISTS sandbox.e1_sim_run_manifest (
    sim_run_id                      VARCHAR PRIMARY KEY,
    started_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at                    TIMESTAMP,
    start_date                      DATE,
    end_date                        DATE,
    initial_capital                 DOUBLE,
    final_capital                   DOUBLE,
    total_trades                    INTEGER,
    win_rate                        DOUBLE,
    total_return_pct                DOUBLE,
    cagr                            DOUBLE,
    inject_scenario                 VARCHAR,  -- populated for stress test runs
    data_coverage_flags             VARCHAR,  -- JSON of which signals were neutralized
    notes                           VARCHAR
);
