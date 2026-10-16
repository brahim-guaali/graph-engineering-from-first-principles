"""Scripted answers for the two judgments, keyed by schema name."""


def answers(kind="refund_request", order_id="A-7731", action="refund", amount=640, clause="§1"):
    def answer(schema, prompt):
        if schema == "Classification":
            return {"kind": kind, "order_id": order_id, "items_claimed": []}
        return {"action": action, "amount": amount, "clause": clause,
                "reason": "late", "customer_message": "Your refund is on its way."}
    return answer
