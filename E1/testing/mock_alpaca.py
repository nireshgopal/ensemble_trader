"""
MockAlpacaClient — E1 Shadow Mode Testing
==========================================
A stateful drop-in replacement for TradingClient and StockHistoricalDataClient.

All API calls are intercepted and redirected to DuckDB sim tables.
No network calls are made. Every method signature matches the real Alpaca SDK.

Usage:
    from E1.testing.mock_alpaca import MockAlpacaClient
    mock = MockAlpacaClient(conn, sim_date=date(2026, 3, 15), initial_cash=50000.0)
    run_e1_trader(_client=mock, _conn=conn)
"""
import uuid
import logging
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import os
logger = logging.getLogger('mock_alpaca')


# =============================================================================
# MOCK DATA OBJECTS
# These mimic the Alpaca SDK's return types so the trader code runs unchanged.
# =============================================================================

@dataclass
class MockAccount:
    cash: str
    buying_power: str
    portfolio_value: str
    equity: str
    long_market_value: str
    last_equity: str
    account_number: str = 'SIM-ACCOUNT'
    status: Any = None

    def __post_init__(self):
        from alpaca.trading.enums import AccountStatus
        self.status = AccountStatus.ACTIVE


@dataclass
class MockPosition:
    symbol: str
    qty: str
    avg_entry_price: str
    market_value: str
    unrealized_pl: str = '0.00'
    unrealized_plpc: str = '0.00'
    current_price: str = '0.00'
    side: str = 'long'


@dataclass
class MockOrder:
    id: str
    client_order_id: str
    symbol: str
    qty: str
    side: Any
    type: Any
    status: Any
    limit_price: Optional[str] = None
    stop_price: Optional[str] = None
    filled_qty: str = '0'
    filled_avg_price: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    legs: List = field(default_factory=list)

    def __post_init__(self):
        from alpaca.trading.enums import OrderStatus
        if isinstance(self.status, str):
            self.status = OrderStatus.FILLED


@dataclass
class MockQuote:
    ask_price: float
    bid_price: float
    ask_size: int = 100
    bid_size: int = 100


# =============================================================================
# MOCK ALPACA CLIENT
# =============================================================================

class MockAlpacaClient:
    """
    Stateful mock that replaces TradingClient for shadow mode.

    State is persisted in DuckDB sandbox.e1_sim_* tables, so:
      - submit_order() writes to e1_sim_order_history
      - get_all_positions() reads from e1_sim_positions
      - The trader and reconciler share the same state via the same conn object
    """

    def __init__(self, conn, sim_date: date, initial_cash: float = 50_000.0,
                 sim_run_id: str = None, inject_scenario: str = None):
        self._conn = conn
        self._sim_date = sim_date
        self._sim_run_id = sim_run_id or str(uuid.uuid4())[:8]
        self._inject_scenario = inject_scenario  # e.g. 'oco-failure', 'zero-price-guard'
        self._initial_cash = initial_cash
        self._call_log: List[Dict] = []  # Full audit trail of every API call
        
        # Performance Optimization: Cache quotes for the current sim_date
        self._quote_cache = {}
        self._cache_date = None

        current_cash = self._get_cash()
        logger.info(f"[MOCK] MockAlpacaClient initialized | sim_date={sim_date} | "
                    f"cash=${current_cash:,.2f} | run={self._sim_run_id} | inject={inject_scenario}")

    # -------------------------------------------------------------------------
    # ACCOUNT
    # -------------------------------------------------------------------------

    def get_account(self) -> MockAccount:
        """Returns simulated account state derived from sim_positions table."""
        self._log('get_account')
        invested = self._get_invested_value()
        cash = self._get_cash()
        portfolio = cash + invested
        return MockAccount(
            cash=str(round(cash, 2)),
            buying_power=str(round(cash, 2)),
            portfolio_value=str(round(portfolio, 2)),
            equity=str(round(portfolio, 2)),
            long_market_value=str(round(invested, 2)),
            last_equity=str(round(portfolio, 2)),
        )

    # -------------------------------------------------------------------------
    # POSITIONS
    # -------------------------------------------------------------------------

    def get_all_positions(self) -> List[MockPosition]:
        """Reads open positions from e1_sim_positions."""
        self._log('get_all_positions')
        rows = self._conn.execute("""
            SELECT ticker, shares, entry_price, dollar_value
            FROM sandbox.e1_sim_positions
            WHERE status = 'OPEN' AND sim_run_id = ?
        """, [self._sim_run_id]).fetchall()

        positions = []
        for ticker, shares, entry_price, dollar_value in rows:
            # Get current price from price history for this sim_date
            current_price = self._get_price(ticker)
            market_val = current_price * shares if current_price else dollar_value
            unreal_pl = market_val - dollar_value
            unreal_plpc = unreal_pl / dollar_value if dollar_value else 0

            positions.append(MockPosition(
                symbol=ticker,
                qty=str(shares),
                avg_entry_price=str(round(entry_price, 4)),
                market_value=str(round(market_val, 2)),
                unrealized_pl=str(round(unreal_pl, 2)),
                unrealized_plpc=str(round(unreal_plpc, 4)),
                current_price=str(round(current_price, 2)) if current_price else '0.00',
            ))
        return positions

    def get_open_position(self, symbol_or_asset_id: str) -> Optional[MockPosition]:
        """Get a single open position by ticker."""
        self._log('get_open_position', symbol=symbol_or_asset_id)
        all_pos = self.get_all_positions()
        for p in all_pos:
            if p.symbol == symbol_or_asset_id:
                return p
        raise Exception(f"position does not exist for {symbol_or_asset_id}")

    # -------------------------------------------------------------------------
    # ORDERS
    # -------------------------------------------------------------------------

    def submit_order(self, order_request) -> MockOrder:
        """
        Intercepts all order submissions.
        - Writes to e1_sim_order_history
        - On BUY: inserts a row into e1_sim_positions
        - On SELL: closes/updates the e1_sim_positions row
        - Supports failure injection via inject_scenario
        """
        self._log('submit_order', request=str(type(order_request).__name__))

        from alpaca.trading.enums import OrderSide, OrderClass, OrderType

        symbol = order_request.symbol
        qty = int(order_request.qty)
        side = order_request.side
        time_in_force = order_request.time_in_force
        order_class = getattr(order_request, 'order_class', None)
        limit_price = getattr(order_request, 'limit_price', None)
        stop_price = None

        # Detect stop_loss nested object
        stop_loss_req = getattr(order_request, 'stop_loss', None)
        if stop_loss_req:
            stop_price = getattr(stop_loss_req, 'stop_price', None)

        # --- INJECT: OCO Failure scenario ---
        if self._inject_scenario == 'oco-failure' and order_class == OrderClass.OCO:
            logger.warning(f"[INJECT] Simulating OCO failure for {symbol}")
            raise Exception('{"code":42210000,"message":"take_profit.limit_price must be < stop_loss.stop_price"}')

        # --- INJECT: Zero-price guard ---
        if self._inject_scenario == 'zero-price-guard':
            logger.warning(f"[INJECT] Simulating zero-price quote for {symbol}")
            return self._record_order(symbol, qty, side, 0.0, limit_price, stop_price,
                                      order_class, status='skipped_zero_price')

        # Get the execution price (use limit_price, or current market price)
        exec_price = float(limit_price) if limit_price else self._get_price(symbol)
        if not exec_price:
            exec_price = 0.0

        # Protective orders (Sell stops/limits) should stay OPEN in simulation
        # until triggered (which we don't simulate intraday).
        # Regular market sells/buys should be FILLED immediately.
        initial_status = 'filled'
        if side == OrderSide.SELL and (limit_price or stop_price):
            initial_status = 'open'

        order = self._record_order(symbol, qty, side, exec_price, limit_price,
                                   stop_price, order_class, status=initial_status)

        # --- Update sim positions state ---
        # NOTE: We do NOT call _open_position/_close_position here because 
        # the real e1_trader (when simulate=False) handles its own DB logging.
        # The mock client's job is just to record the order history.
        
        # --- PHASE 2: BRACKET LEGS (Plumbing Test) ---
        # If it's a bracket order, we record the LEGS as OPEN orders so the 
        # reconciler sees them as existing protective stops/targets.
        if order_class == OrderClass.BRACKET:
            # 1. Stop Loss Leg
            if stop_price:
                self._record_order(symbol, qty, OrderSide.SELL, 0.0, None, stop_price, 
                                   OrderClass.BRACKET, status='open')
            # 2. Take Profit Leg
            if limit_price:
                self._record_order(symbol, qty, OrderSide.SELL, 0.0, limit_price, None,
                                   OrderClass.BRACKET, status='open')

        return order

    def get_orders(self, filter=None) -> List[MockOrder]:
        """Returns orders from e1_sim_order_history for this run."""
        self._log('get_orders')
        from alpaca.trading.enums import OrderSide, OrderType, OrderStatus

        # Handle status and side filtering from the request
        status_filter = ""
        if filter and hasattr(filter, 'status') and filter.status:
            from alpaca.trading.enums import QueryOrderStatus
            if filter.status == QueryOrderStatus.OPEN:
                status_filter = "AND status = 'open'"
            elif filter.status == QueryOrderStatus.CLOSED:
                status_filter = "AND status IN ('filled', 'cancelled')"
        else:
            # Default: only return truly OPEN orders unless a specific filter is provided
            status_filter = "AND status = 'open'"

        side_filter = ""
        if filter and hasattr(filter, 'side') and filter.side:
            from alpaca.trading.enums import OrderSide
            side_str = 'buy' if filter.side == OrderSide.BUY else 'sell'
            side_filter = f"AND side = '{side_str}'"

        query = f"""
            SELECT order_id, client_order_id, ticker, side, qty, status,
                   filled_qty, filled_avg_price, limit_price, stop_price, order_class
            FROM sandbox.e1_sim_order_history
            WHERE sim_run_id = ? {status_filter} {side_filter}
            ORDER BY submitted_at DESC
        """
        
        params = [self._sim_run_id]
        if "sim_date = ?" in status_filter:
            params.append(self._sim_date)
            
        rows = self._conn.execute(query, params).fetchall()

        orders = []
        from alpaca.trading.enums import OrderStatus
        for row in rows:
            order_id, coid, ticker, side, qty, status, fq, fp, lp, sp, oc = row
            
            # Map DB status string to OrderStatus enum
            alpaca_status = OrderStatus.FILLED
            if status == 'open':
                alpaca_status = OrderStatus.NEW
            elif status == 'cancelled':
                alpaca_status = OrderStatus.CANCELED

            # If it's a consolidated order (OCO or Bracket with both), 
            # we return virtual legs so the reconciler's simple type-checks pass.
            if lp and sp:
                # Limit Leg
                orders.append(MockOrder(
                    id=f"{order_id}-L", client_order_id=coid, symbol=ticker, qty=str(qty),
                    side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                    type='limit', status=alpaca_status, limit_price=str(lp), stop_price=None,
                    filled_qty=str(fq or qty) if alpaca_status == OrderStatus.FILLED else '0',
                    filled_avg_price=str(fp) if fp else None,
                ))
                # Stop Leg
                orders.append(MockOrder(
                    id=f"{order_id}-S", client_order_id=coid, symbol=ticker, qty=str(qty),
                    side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                    type='stop', status=alpaca_status, limit_price=None, stop_price=str(sp),
                    filled_qty=str(fq or qty) if alpaca_status == OrderStatus.FILLED else '0',
                    filled_avg_price=str(fp) if fp else None,
                ))
            else:
                orders.append(MockOrder(
                    id=order_id,
                    client_order_id=coid,
                    symbol=ticker,
                    qty=str(qty),
                    side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                    type='limit' if lp else ('stop' if sp else 'market'),
                    status=alpaca_status,
                    limit_price=str(lp) if lp else None,
                    stop_price=str(sp) if sp else None,
                    filled_qty=str(fq or qty) if alpaca_status == OrderStatus.FILLED else '0',
                    filled_avg_price=str(fp) if fp else None,
                ))
        return orders

    def cancel_order_by_id(self, order_id: str):
        """Marks an order as cancelled in the sim log. Handles virtual leg IDs."""
        self._log('cancel_order_by_id', order_id=order_id)
        
        # V1.3 FIX: Strip virtual leg suffixes (-L, -S) to find the base order in DB
        base_id = order_id
        if order_id.endswith('-L') or order_id.endswith('-S'):
            base_id = order_id[:-2]
            
        self._conn.execute("""
            UPDATE sandbox.e1_sim_order_history
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ? AND sim_run_id = ?
        """, [base_id, self._sim_run_id])

    def cancel_orders_for_symbol(self, symbol: str):
        """Cancels all open orders for a symbol."""
        self._log('cancel_orders_for_symbol', symbol=symbol)
        self._conn.execute("""
            UPDATE sandbox.e1_sim_order_history
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE ticker = ? AND sim_run_id = ? AND status NOT IN ('filled','cancelled')
        """, [symbol, self._sim_run_id])

    # -------------------------------------------------------------------------
    # DATA CLIENT METHODS (for live quote fetching)
    # -------------------------------------------------------------------------

    def get_stock_latest_quote(self, request) -> Dict[str, MockQuote]:
        return self.get_latest_quote(request)

    def get_latest_quote(self, request) -> Dict[str, MockQuote]:
        """
        Returns historical close price as the mock quote.
        Used by the trader's live-price fetch before entry.
        """
        symbols = request.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]

        self._log('get_stock_latest_quote', symbols=symbols)
        result = {}
        for sym in symbols:
            price = self._get_price(sym)
            if price:
                # Inject zero-price if scenario is set
                if self._inject_scenario == 'zero-price-guard':
                    price = 0.0
                result[sym] = MockQuote(ask_price=price, bid_price=price * 0.999)
        return result

    # -------------------------------------------------------------------------
    # INTERNAL STATE HELPERS
    # -------------------------------------------------------------------------

    def _get_price(self, ticker: str) -> Optional[float]:
        """Fetches the closing price for the sim_date with bulk caching."""
        sim_date_str = str(self._sim_date)
        if self._cache_date != sim_date_str:
            # Pre-fetch ALL prices for the day to avoid individual queries
            res = self._conn.execute("""
                SELECT ticker, close FROM refined.price_history 
                WHERE date = ?
            """, [sim_date_str]).fetchall()
            self._quote_cache = {t: p for t, p in res}
            self._cache_date = sim_date_str
            logger.debug(f"[MOCK] Cached {len(self._quote_cache)} quotes for {sim_date_str}")

        price = self._quote_cache.get(ticker)
        return float(price) if price is not None else None

    def _get_cash(self) -> float:
        """
        Computes available cash = initial_capital + realized_pnl - sum(entry_dollar_values).
        """
        # Sum of entry_dollar_value for all open positions
        entry_invested_row = self._conn.execute("""
            SELECT COALESCE(SUM(dollar_value), 0)
            FROM sandbox.e1_sim_positions
            WHERE sim_run_id = ? AND status = 'OPEN'
        """, [self._sim_run_id]).fetchone()
        entry_invested = float(entry_invested_row[0]) if entry_invested_row else 0.0

        realized_pnl = self._conn.execute("""
            SELECT COALESCE(SUM(pnl_dollars), 0)
            FROM sandbox.e1_sim_positions
            WHERE sim_run_id = ? AND status = 'CLOSED'
        """, [self._sim_run_id]).fetchone()[0]
        
        return self._initial_cash + float(realized_pnl) - entry_invested

    def _get_invested_value(self) -> float:
        """Mark-to-market sum of all open positions in this run."""
        rows = self._conn.execute("""
            SELECT ticker, shares, entry_price 
            FROM sandbox.e1_sim_positions 
            WHERE status = 'OPEN' AND sim_run_id = ?
        """, [self._sim_run_id]).fetchall()
        
        total_mv = 0.0
        for ticker, shares, entry_price in rows:
            price = self._get_price(ticker)
            if price is None:
                price = entry_price
            total_mv += float(shares) * float(price)
        return total_mv

    def _open_position(self, ticker: str, qty: int, price: float):
        """Inserts a new OPEN position into e1_sim_positions."""
        dollar_val = qty * price
        # Generate sequential sim ID
        max_id = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM sandbox.e1_sim_positions"
        ).fetchone()[0]
        new_id = int(max_id) + 1

        self._conn.execute("""
            INSERT INTO sandbox.e1_sim_positions
                (id, ticker, status, entry_date, entry_price, shares, dollar_value,
                 created_at, updated_at, sim_run_id, sim_date)
            VALUES (?, ?, 'OPEN', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
        """, [new_id, ticker, self._sim_date, price, qty, dollar_val,
              self._sim_run_id, self._sim_date])
        logger.debug(f"[MOCK] Opened position: {ticker} x{qty} @ ${price:.2f}")

    def _close_position(self, ticker: str, qty: int, price: float, trigger: str = 'SELL'):
        """Closes (or partially closes) an open position in e1_sim_positions."""
        row = self._conn.execute("""
            SELECT id, entry_price, shares, dollar_value
            FROM sandbox.e1_sim_positions
            WHERE ticker = ? AND status = 'OPEN' AND sim_run_id = ?
            ORDER BY entry_date ASC LIMIT 1
        """, [ticker, self._sim_run_id]).fetchone()

        if not row:
            logger.warning(f"[MOCK] close_position: {ticker} not found in open sim positions")
            return

        pos_id, entry_price, shares, dollar_val = row
        pnl_pct = (price - entry_price) / entry_price if entry_price else 0
        pnl_dollars = pnl_pct * dollar_val

        self._conn.execute("""
            UPDATE sandbox.e1_sim_positions
            SET status = 'CLOSED', exit_date = ?, exit_price = ?,
                exit_trigger = ?, pnl_pct = ?, pnl_dollars = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND sim_run_id = ?
        """, [self._sim_date, price, trigger, pnl_pct, pnl_dollars,
              pos_id, self._sim_run_id])
        logger.debug(f"[MOCK] Closed position: {ticker} @ ${price:.2f} | PnL: {pnl_pct*100:.2f}%")

    def _record_order(self, symbol, qty, side, exec_price, limit_price,
                      stop_price, order_class, status='filled') -> MockOrder:
        """Writes an order record to e1_sim_order_history."""
        from alpaca.trading.enums import OrderSide
        order_id = str(uuid.uuid4())
        client_order_id = f"sim-{self._sim_run_id}-{symbol}-{str(uuid.uuid4())[:6]}"
        side_str = 'buy' if side == OrderSide.BUY else 'sell'
        oc_str = str(order_class.value) if order_class else 'market'

        self._conn.execute("""
            INSERT INTO sandbox.e1_sim_order_history
                (order_id, client_order_id, ticker, side, qty, status,
                 filled_qty, filled_avg_price, limit_price, stop_price,
                 order_class, submitted_at, updated_at, sim_run_id, sim_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """, [order_id, client_order_id, symbol, side_str, qty, status,
              qty, exec_price, limit_price, stop_price, oc_str,
              self._sim_date, self._sim_run_id, self._sim_date])

        from alpaca.trading.enums import OrderSide as OS, OrderType, OrderStatus
        return MockOrder(
            id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            qty=str(qty),
            side=side,
            type='limit' if limit_price else ('stop' if stop_price else 'market'),
            status=OrderStatus.FILLED,
            limit_price=str(limit_price) if limit_price else None,
            stop_price=str(stop_price) if stop_price else None,
            filled_qty=str(qty),
            filled_avg_price=str(round(exec_price, 4)),
        )

    def _log(self, method: str, **kwargs):
        """Audit trail for every API call made during the sim."""
        entry = {'method': method, 'sim_date': str(self._sim_date), **kwargs}
        self._call_log.append(entry)
        logger.debug(f"[MOCK API] {method}({kwargs})")

    # -------------------------------------------------------------------------
    # SIMULATION UTILITIES
    # -------------------------------------------------------------------------

    @property
    def call_log(self) -> List[Dict]:
        """Returns the full audit trail of every API call made."""
        return self._call_log

    @property
    def sim_run_id(self) -> str:
        return self._sim_run_id

    def get_portfolio_snapshot(self) -> Dict:
        """Convenience method for the runner to snapshot the equity curve."""
        account = self.get_account()
        positions = self.get_all_positions()
        return {
            'portfolio_value': float(account.portfolio_value),
            'cash': float(account.cash),
            'invested': float(account.long_market_value),
            'open_positions': len(positions),
        }
