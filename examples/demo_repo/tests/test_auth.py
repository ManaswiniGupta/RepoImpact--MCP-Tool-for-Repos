from auth import create_token, login


def test_login_success():
    token = login("alice", "wonderland")
    assert token == create_token("alice")


def test_login_invalid_password():
    try:
        login("alice", "wrong")
        assert False, "expected ValueError"
    except ValueError:
        pass
