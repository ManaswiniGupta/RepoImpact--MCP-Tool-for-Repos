"""In-memory data access layer for the demo application."""


class Database:
    def __init__(self):
        self._users = {"alice": {"username": "alice", "password": "wonderland"}}
        self._payments = []

    def get_user(self, username):
        return self._users.get(username)

    def save_payment(self, payment):
        self._payments.append(payment)
        return payment


db = Database()
