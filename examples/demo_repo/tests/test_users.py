from users import validate_user


def test_validate_user_true():
    assert validate_user("alice", "wonderland") is True


def test_validate_user_false():
    assert validate_user("alice", "wrong") is False
