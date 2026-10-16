import httpx


def test_order_has_lateness_computed(platform):
    order = httpx.get(f"{platform}/orders/A-7731").json()
    assert order["total"] == 640 and order["minutes_late"] == 70


def test_payment_with_same_idempotency_key_moves_money_once(platform):
    for _ in range(3):
        httpx.post(f"{platform}/payments", json={"order_id": "A-7731", "amount": 640},
                   headers={"Idempotency-Key": "run-1:refund"})
    assert httpx.get(f"{platform}/payments", params={"order_id": "A-7731"}).json()["count"] == 1


def test_payment_without_key_moves_money_every_time(platform):
    for _ in range(2):
        httpx.post(f"{platform}/payments", json={"order_id": "A-7731", "amount": 640})
    assert httpx.get(f"{platform}/payments", params={"order_id": "A-7731"}).json()["total"] == 1280


def test_approval_flow(platform):
    a = httpx.post(f"{platform}/approvals", json={"run_id": "r1", "order_id": "A-7731",
                                                 "amount": 640, "reason": "late"}).json()
    assert a["status"] == "pending"
    d = httpx.post(f"{platform}/approvals/{a['id']}/decision", json={"decision": "approved"}).json()
    assert d["status"] == "approved"


def test_injected_fault_hangs_the_next_call(platform):
    httpx.post(f"{platform}/admin/faults", json={"fraud": 1})
    try:
        httpx.get(f"{platform}/fraud/C-1042", timeout=0.5)
        assert False, "should have timed out"
    except httpx.TimeoutException:
        pass
    assert httpx.get(f"{platform}/fraud/C-1042", timeout=2).json()["score"] < 0.7
