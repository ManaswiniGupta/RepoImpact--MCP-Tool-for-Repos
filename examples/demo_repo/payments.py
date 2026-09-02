from database import db


class PaymentRepository:
    def save(self, payment):
        return db.save_payment(payment)


def process_payment(order_id, amount):
    repo = PaymentRepository()
    return repo.save({"order_id": order_id, "amount": amount})


def checkout(order_id, amount):
    return process_payment(order_id, amount)
