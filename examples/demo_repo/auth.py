from users import validate_user


def create_token(username):
    return f"token-for-{username}"


def login(username, password):
    if not validate_user(username, password):
        raise ValueError("invalid credentials")
    return create_token(username)
