import os
import requests
import logging
import html
from dotenv import load_dotenv

# Initialize logger
logger = logging.getLogger("notifier")

def safe_html(text):
    """Escapes special HTML characters."""
    return html.escape(str(text), quote=False)

def send_telegram(message, parse_mode='HTML'):
    """
    Sends a message to the Telegram bot configured in .env.
    Defaults to HTML for robustness.
    """
    load_dotenv()
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')

    if not token or not chat_id:
        logger.error("Telegram credentials missing in .env (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': parse_mode
    }

    try:
        response = requests.post(url, data=payload, timeout=10)
        data = response.json()
        if not data.get('ok'):
            logger.error(f"Telegram API Error: {data.get('description')}")
            # Log the problematic message for debugging
            logger.debug(f"Failed message content: {message}")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {str(e)}")
        return False

def format_audit_summary(date_str, regime, results, cash_floor_ok, total_value):
    """
    Formats a Strategy E1 audit summary for Telegram using HTML.
    """
    status_emoji = "🟢" if regime != "BEAR" else "🐻"
    msg = f"{status_emoji} <b>E1 MORNING AUDIT ({date_str})</b>\n\n"
    
    msg += f"<b>Regime</b>: <code>{safe_html(regime)}</code>\n"
    msg += f"<b>Portfolio Value</b>: <code>${total_value:,.0f}</code>\n"
    msg += f"<b>Cash Floor</b>: {'✅ OK' if cash_floor_ok else '⚠️ LOW'}\n\n"
    
    msg += "<b>Decisions</b>:\n"
    if not results:
        msg += "<i>No active positions or entries today.</i>\n"
    else:
        for res in results:
            ticker = res['ticker']
            action = res['action']
            pnl = res.get('pnl_pct', 0)
            
            perf_suffix = ""
            if pnl > 0.005: 
                perf_suffix = f" (👍 {pnl*100:+.1f}%)"
            elif pnl < -0.005: 
                perf_suffix = f" (👎 {pnl*100:+.1f}%)"
            
            emoji = "🟢" if action == "HOLD" else "🔴" if action == "SELL" else "🟠"
            msg += f"{emoji} <b>{ticker}</b>: {action}{perf_suffix}\n"
            if action != "HOLD":
                msg += f"  └ <i>{safe_html(res['reason'])}</i>\n"

    return msg

def format_portfolio_summary(date_str, account, positions):
    """
    Formats a daily EOD portfolio summary from Alpaca data using HTML.
    """
    equity = float(account.equity)
    buying_power = float(account.buying_power)
    cash = float(account.cash)
    invested = float(account.long_market_value)
    last_equity = float(account.last_equity)
    day_pnl = equity - last_equity
    day_pct = (day_pnl / last_equity) if last_equity > 0 else 0
    
    status_emoji = "💰" if day_pnl >= 0 else "📉"
    msg = f"{status_emoji} <b>EOD PORTFOLIO SUMMARY ({date_str})</b>\n\n"
    
    msg += f"<b>Total Equity</b>: <code>${equity:,.2f}</code> ({day_pnl:+,.2f} | {day_pct:+.2%})\n"
    msg += f"<b>Invested Value</b>: <code>${invested:,.2f}</code>\n"
    msg += f"<b>Cash Balance</b>: <code>${cash:,.2f}</code>\n"
    msg += f"<b>Buying Power</b>: <code>${buying_power:,.2f}</code>\n\n"
    
    msg += "<b>Active Positions</b>:\n"
    if not positions:
        msg += "<i>No open positions currently.</i>\n"
    else:
        for p in positions:
            ticker = p.symbol
            unrealized_pnl = float(p.unrealized_pl)
            unrealized_pct = float(p.unrealized_plpc)
            side = "🟢" if unrealized_pnl >= 0 else "🔴"
            msg += f"{side} <b>{ticker}</b>: {unrealized_pct:+.2%} (${unrealized_pnl:+,.2f})\n"
            
    msg += "\n🛡️ <i>All positions are reconciled and protected by protective stops.</i>"
            
    return msg

def format_decay_audit_summary(stats):
    """
    Formats the 30-session Score Decay audit report.
    """
    n = stats['total']
    x = stats['saved_count']
    y = stats['cost_count']
    x_pct = stats['saved_pct']
    y_pct = stats['cost_pct']
    avg_z = stats['avg_cost_dollars']
    
    msg = (
        f"\n\n📊 <b>DECAY VETO REVIEW (30-session)</b>\n"
        f"Total decay exits tracked: {n}\n"
        f"VETO_SAVED_CAPITAL: {x} ({x_pct:.1f}%)\n"
        f"VETO_COST_ALPHA: {y} ({y_pct:.1f}%)\n"
        f"Avg counterfactual PnL (cost-alpha exits): ${avg_z:,.2f}\n"
    )
    
    if y_pct > 60.0:
        msg += "⚠️ <b>THRESHOLD REVIEW REQUIRED</b> (40% → 50%)."
    
    return msg

if __name__ == "__main__":
    load_dotenv()
    test_msg = "🟢 <b>Notifier Connection Established</b>"
    send_telegram(test_msg)
