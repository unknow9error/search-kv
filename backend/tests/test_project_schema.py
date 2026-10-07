"""Validate independent public observations and the additive catalog migration."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain import project_models  # noqa: F401
from app.domain.project_input import ProjectInput, ProjectLayoutInput
from app.domain.project_schemas import (
    ProjectBounds,
    ProjectCompareRequest,
    ProjectCriteria,
    ProjectFacets,
    ProjectPrice,
    ProjectTurnRequest,
)


def public_project(**changes):
    data = {
        "external_id": "project-1",
        "name": "Public project",
        "city": "astana",
        "source_url": "https://example.com/project-1",
        "observed_at": datetime.now(UTC),
    }
    return ProjectInput.model_validate({**data, **changes})


def test_project_without_lots_does_not_require_or_invent_sale_information():
    project = public_project()
    assert project.city == "Астана"
    assert project.published_starting_price_kzt is None
    assert project.layouts == []
    assert project.completion_date is None
    assert project.stage is None
    assert project.developer_name is None
    with pytest.raises(ValidationError, match="Extra inputs"):
        public_project(observed_listing_minimum=12_000_000)
    with pytest.raises(ValidationError):
        public_project(stage="maybe ready")
    assert public_project(stage="commissioned").stage == "commissioned"


def test_public_project_cannot_claim_derived_listing_namespace():
    with pytest.raises(ValidationError, match="reserved"):
        public_project(external_id="listing:apartment-1")
    with pytest.raises(ValidationError, match="reserved"):
        public_project(external_id="  listing:apartment-1  ")
    assert public_project(external_id="project:apartment-1").external_id == "project:apartment-1"


def test_browsable_website_requires_scope_without_replacing_exact_source():
    project = public_project(
        source_url="https://api.example.com/public/projects/1",
        website_url="https://example.com/projects/1",
        website_scope="project",
    )
    assert str(project.source_url) == "https://api.example.com/public/projects/1"
    assert str(project.website_url) == "https://example.com/projects/1"
    assert project.website_scope == "project"
    assert public_project().website_url is None
    for values in (
        {"website_url": "https://example.com/project"},
        {"website_scope": "project"},
        {"website_url": "https://example.com", "website_scope": "generic"},
        {"website_url": "https://localhost", "website_scope": "project"},
    ):
        with pytest.raises(ValidationError):
            public_project(**values)


def test_public_project_preserves_only_supplied_bigville_metadata():
    assert public_project().bigville_id is None
    assert public_project().bigville_name is None
    project = public_project(bigville_id="bv-1", bigville_name="Published district")
    assert project.bigville_id == "bv-1"
    assert project.bigville_name == "Published district"
    for values in (
        {"bigville_id": ""},
        {"bigville_id": "x" * 121},
        {"bigville_name": "x" * 201},
    ):
        with pytest.raises(ValidationError):
            public_project(**values)


def test_facets_separate_unknown_price_counts_from_selectable_modes():
    facets = ProjectFacets(
        price_modes=[
            {"value": "published_starting_price", "count": 2},
            {"value": "observed_listing_minimum", "count": 3},
        ],
        unknown_published_price_count=4,
        unknown_observed_price_count=3,
    )
    assert len(facets.price_modes) == 2
    assert facets.unknown_published_price_count == 4
    for values in (
        {"price_modes": [{"value": "unknown", "count": 2}]},
        {"price_modes": [{"value": "published_starting_price", "count": 2}] * 2},
        {"unknown_published_price_count": -1},
        {"unknown_observed_price_count": -1},
    ):
        with pytest.raises(ValidationError):
            ProjectFacets(**values)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/project-1",
        "https://user:password@example.com/project-1",
        "https://user@example.com/project-1",
        "https://localhost/project-1",
        "https://sub.localhost/project-1",
        "https://host.local/project-1",
        "https://127.0.0.1/project-1",
        "https://10.0.0.1/project-1",
        "https://169.254.169.254/project-1",
        "https://[::1]/project-1",
    ],
)
def test_project_requires_public_https_source_without_credentials(url):
    with pytest.raises(ValidationError):
        public_project(source_url=url)


@pytest.mark.parametrize(
    "observed_at",
    [datetime.now(UTC).replace(tzinfo=None), datetime.now(UTC) + timedelta(hours=1)],
)
def test_project_requires_aware_nonfuture_source_date(observed_at):
    with pytest.raises(ValidationError):
        public_project(observed_at=observed_at)


def test_partial_coordinates_and_duplicate_layouts_are_rejected():
    with pytest.raises(ValidationError, match="pair"):
        public_project(latitude=51.12)
    layout = {
        "external_id": "layout-1",
        "name": "Two rooms",
        "rooms": 2,
        "source_url": "https://example.com/layout-1",
        "observed_at": datetime.now(UTC),
    }
    with pytest.raises(ValidationError, match="Duplicate"):
        public_project(layouts=[layout, layout])


def test_layout_type_rejects_price_status_and_floor_without_concrete_lot():
    layout = {
        "external_id": "layout-1",
        "name": "Two rooms",
        "rooms": 2,
        "source_url": "https://example.com/layout-1",
        "observed_at": datetime.now(UTC),
    }
    assert ProjectLayoutInput.model_validate(layout).area_m2 is None
    for field, value in {"price_kzt": 30_000_000, "status": "available", "floor": 2}.items():
        with pytest.raises(ValidationError, match="Extra inputs"):
            ProjectLayoutInput.model_validate({**layout, field: value})


def test_search_has_distinct_price_mode_and_valid_ranges():
    criteria = ProjectCriteria(rooms=[2, 1, 2], required_amenities=["school", "school"])
    assert criteria.price_mode == "published_starting_price"
    assert criteria.rooms == [1, 2]
    assert criteria.required_amenities == ["school"]
    for values in (
        {"price_min": 40_000_000, "price_max": 30_000_000},
        {"floor_min": 5, "floor_max": 2},
        {"rooms": [0]},
        {"area_min": float("nan")},
        {"districts": ["   "]},
    ):
        with pytest.raises(ValidationError):
            ProjectCriteria(**values)
    with pytest.raises(ValidationError):
        ProjectBounds(south=52, north=51, west=70, east=72)


def test_known_price_requires_provenance_unknown_price_has_no_amount():
    assert ProjectPrice(kind="unknown").amount_kzt is None
    with pytest.raises(ValidationError):
        ProjectPrice(kind="unknown", amount_kzt=1)
    with pytest.raises(ValidationError):
        ProjectPrice(kind="published_starting_price", amount_kzt=30_000_000)
    value = ProjectPrice(
        kind="observed_listing_minimum",
        amount_kzt=30_000_000,
        source_url="https://example.com/listing",
        observed_at=datetime.now(UTC),
    )
    assert value.kind == "observed_listing_minimum"


def test_compare_and_turn_require_real_distinct_selection():
    project_id = uuid4()
    with pytest.raises(ValidationError):
        ProjectCompareRequest(project_ids=[project_id, project_id])
    with pytest.raises(ValidationError):
        ProjectTurnRequest(client_turn_id=uuid4(), message="Compare", action="compare")
    with pytest.raises(ValidationError):
        ProjectTurnRequest(client_turn_id=uuid4(), message="Explain", action="explain")
    with pytest.raises(ValidationError):
        ProjectTurnRequest(client_turn_id=uuid4(), message=" ", selected_project_ids=[project_id])


def test_project_migration_is_additive_reversible_and_enforces_private_cascade(tmp_path, monkeypatch):
    database = tmp_path / "catalog-migration.sqlite3"
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("MEKEN_ENV", "test")
    monkeypatch.setenv("MEKEN_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))

    try:
        command.upgrade(config, "0008_account_recovery")
        user_id, other_user_id = str(uuid4()), str(uuid4())
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO users(id,created_at) VALUES (?,CURRENT_TIMESTAMP),(?,CURRENT_TIMESTAMP)",
                (user_id, other_user_id),
            )
            connection.execute(
                "INSERT INTO providers(id,name,demo,enabled) VALUES ('source-a','Public source',0,1)"
            )
        command.upgrade(config, "0009_project_catalog")
        command.check(config)
        project_id, conversation_id = str(uuid4()), str(uuid4())
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO projects "
                "(id,provider_id,external_id,name,city,district,source_url,observed_at,received_at,"
                "record_origin,version,content_hash,data) "
                "VALUES (?,'source-a','p1','Project','Астана','','https://example.com/p1',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'public_project',1,?,'{}')",
                (project_id, "a" * 64),
            )
            connection.execute(
                "INSERT INTO project_favorites(user_id,project_id,created_at) VALUES (?,?,CURRENT_TIMESTAMP)",
                (user_id, project_id),
            )
            key = str(uuid4())
            connection.execute(
                "INSERT INTO project_conversations "
                "(id,user_id,client_conversation_id,initial_criteria_hash,title,criteria,created_at,updated_at) "
                "VALUES (?,?,?,?,'Search','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (conversation_id, user_id, key, "b" * 64),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO project_conversations "
                    "(id,user_id,client_conversation_id,initial_criteria_hash,title,criteria,created_at,updated_at) "
                    "VALUES (?,?,?,?,'Search','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (str(uuid4()), user_id, key, "b" * 64),
                )
            # The same client key is valid for a different user.
            connection.execute(
                "INSERT INTO project_conversations "
                "(id,user_id,client_conversation_id,initial_criteria_hash,title,criteria,created_at,updated_at) "
                "VALUES (?,?,?,?,'Other','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (str(uuid4()), other_user_id, key, "b" * 64),
            )
            connection.execute(
                "INSERT INTO project_turns "
                "(id,conversation_id,client_turn_id,message,request_hash,response,state,created_at) "
                "VALUES (?,?,?,'Message',?,'{}','complete',CURRENT_TIMESTAMP)",
                (str(uuid4()), conversation_id, str(uuid4()), "c" * 64),
            )
            connection.execute(
                "INSERT INTO data_reports "
                "(id,user_id,project_id,client_report_id,category,message,created_at) "
                "VALUES (?,?,?,?,'price','Report',CURRENT_TIMESTAMP)",
                (str(uuid4()), user_id, project_id, str(uuid4())),
            )
            connection.execute("DELETE FROM users WHERE id=?", (user_id,))
            assert connection.execute("SELECT COUNT(*) FROM project_favorites").fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM project_turns").fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM data_reports").fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM project_conversations").fetchone() == (1,)
            assert connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (1,)
        command.downgrade(config, "0008_account_recovery")
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT id FROM providers").fetchone() == ("source-a",)
            assert connection.execute("SELECT id FROM users").fetchone() == (other_user_id,)
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone() == (0,)
        command.upgrade(config, "0009_project_catalog")
        command.check(config)
    finally:
        get_settings.cache_clear()
