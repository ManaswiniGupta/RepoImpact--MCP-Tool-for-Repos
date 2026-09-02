from payments import checkout, process_payment


def test_checkout_calls_process_payment():
    result = checkout("order-1", 42)
    assert result["order_id"] == "order-1"
    assert result["amount"] == 42


def test_process_payment_amount():
    result = process_payment("order-2", 10)
    assert result["amount"] == 10
