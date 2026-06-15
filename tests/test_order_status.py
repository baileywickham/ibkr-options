"""_status_dict must surface rejection reasons (from trade.log AND captured
error events), never hide them, and _limit_order must pin the account."""

from ibkr_options import orders


class LogEntry:
    def __init__(self, errorCode, message):
        self.errorCode = errorCode
        self.message = message


class Status:
    def __init__(self, status, filled=0.0, remaining=1.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.remaining = remaining
        self.avgFillPrice = avg


class Order:
    orderId = 7


class Trade:
    def __init__(self, status, log):
        self.order = Order()
        self.orderStatus = status
        self.log = log


def test_working_order_has_no_rejected_flag():
    trade = Trade(Status("PreSubmitted"), [LogEntry(0, "submitted")])
    out = orders._status_dict(trade)
    assert out["status"] == "PreSubmitted"
    assert "rejected" not in out
    assert "messages" not in out  # errorCode 0 is not a message


def test_rejected_order_surfaces_reason_and_flag():
    log = [LogEntry(0, "PendingSubmit"),
           LogEntry(202, "Order Canceled - reason: limit too aggressive")]
    trade = Trade(Status("Cancelled", filled=0.0), log)
    out = orders._status_dict(trade)
    assert out["rejected"] is True
    assert any("202" in m for m in out["messages"])


def test_reason_from_captured_events_is_merged():
    # the real reason (202) often arrives only via ib.errorEvent, not trade.log
    trade = Trade(Status("Cancelled", filled=0.0), [LogEntry(10349, "TIF set to DAY")])
    out = orders._status_dict(trade, collected={202: "Limit price too far outside NBBO"})
    assert out["rejected"] is True
    assert any("202" in m and "NBBO" in m for m in out["messages"])
    assert any("10349" in m for m in out["messages"])


def test_inactive_status_is_rejected():
    trade = Trade(Status("Inactive", filled=0.0), [LogEntry(201, "margin")])
    out = orders._status_dict(trade)
    assert out["rejected"] is True


def test_partial_fill_then_cancel_not_flagged_rejected():
    # filled>0 means it worked at least partially; not a rejection
    trade = Trade(Status("Cancelled", filled=1.0), [LogEntry(0, "x")])
    out = orders._status_dict(trade)
    assert "rejected" not in out
    assert "unconfirmed" not in out


def test_unacknowledged_status_is_flagged_unconfirmed():
    # IBKR never acknowledged it within the settle window
    trade = Trade(Status("PendingSubmit"), [])
    out = orders._status_dict(trade)
    assert out.get("unconfirmed") is True
    assert "rejected" not in out


def test_blank_status_is_flagged_unconfirmed():
    trade = Trade(Status(""), [])
    out = orders._status_dict(trade)
    assert out.get("unconfirmed") is True


def test_working_statuses_are_not_unconfirmed():
    for s in ("Filled", "Submitted", "PreSubmitted"):
        out = orders._status_dict(Trade(Status(s, filled=1.0 if s == "Filled" else 0.0), []))
        assert "unconfirmed" not in out
        assert "rejected" not in out


def test_limit_order_pins_account():
    order = orders._limit_order("U123", "BUY", 1, 1.5, "DAY")
    assert order.account == "U123"


def test_limit_order_without_account_leaves_it_unset():
    order = orders._limit_order(None, "BUY", 1, 1.5, "DAY")
    assert not order.account
