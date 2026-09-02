from pathlib import Path

import pytest

from repoimpact.repository import InvalidRepositoryError, is_github_url, open_repository


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo.git",
        "https://github.com/some-org_1/some.repo-2",
    ],
)
def test_valid_github_urls_accepted(url):
    assert is_github_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ssh://git@github.com/owner/repo.git",
        "git://github.com/owner/repo.git",
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",
        "not-a-url",
        "https://github.com/owner/repo; rm -rf /",
    ],
)
def test_invalid_or_non_github_urls_rejected(url):
    assert is_github_url(url) is False


def test_open_repository_with_local_path_indexes_it(tmp_path):
    _write(tmp_path, "app.py", "def main():\n    pass\n")
    data_dir = tmp_path.parent / f"{tmp_path.name}_data"

    session = open_repository(str(tmp_path), data_dir=data_dir)
    try:
        assert session.root == tmp_path
        files = session.storage.list_files()
        assert {f["path"] for f in files} == {"app.py"}
    finally:
        session.close()


def test_open_repository_rejects_nonexistent_local_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(InvalidRepositoryError):
        open_repository(str(missing), data_dir=tmp_path / "data")


def test_open_repository_rejects_non_github_url(tmp_path):
    with pytest.raises(InvalidRepositoryError):
        open_repository("https://gitlab.com/owner/repo", data_dir=tmp_path / "data")


def test_open_repository_reuses_repo_id_for_same_local_path(tmp_path):
    _write(tmp_path, "app.py", "def main():\n    pass\n")
    data_dir = tmp_path.parent / f"{tmp_path.name}_data2"

    session1 = open_repository(str(tmp_path), data_dir=data_dir)
    repo_id_1 = session1.repo_id
    session1.close()

    session2 = open_repository(str(tmp_path), data_dir=data_dir)
    repo_id_2 = session2.repo_id
    session2.close()

    assert repo_id_1 == repo_id_2
