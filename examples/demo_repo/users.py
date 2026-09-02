from database import db


class UserRepository:
    def get_user(self, username):
        return db.get_user(username)


def validate_user(username, password):
    repo = UserRepository()
    user = repo.get_user(username)
    return user is not None and user["password"] == password
