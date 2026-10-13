from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers models)
from app.config import Config
from app.db import Base, make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        yield db
    engine.dispose()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    (path / "cvs").mkdir(parents=True)
    return path


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """App wired to a fresh data dir, schema created through the real migrations."""
    config = Config(data_dir=tmp_path / "appdata", allowed_hosts="testserver")
    with TestClient(create_app(config)) as test_client:
        yield test_client
