from app.models.tenant import DomainType
from app.schemas.tenant import DEFAULT_DOMAIN_CONFIGS
from app.services.embedding.text_builder import TextBuilder

HR_CONFIG = DEFAULT_DOMAIN_CONFIGS[DomainType.HR].model_dump()
FOOD_CONFIG = DEFAULT_DOMAIN_CONFIGS[DomainType.FOOD].model_dump()

builder = TextBuilder()


def test_hr_primary_field_first_then_searchable_fields_with_prefixes() -> None:
    job = {
        "external_id": "job-1",
        "title": "Backend Engineer",
        "description": "Senior Python Developer building APIs",
        "skills": ["Python", "FastAPI", "Docker"],
        "location": "Bangalore",  # a filter field: not embedded
    }

    text = builder.build_embedding_text(job, HR_CONFIG)

    assert text == (
        "description: Senior Python Developer building APIs "
        "title: Backend Engineer "
        "skills: Python FastAPI Docker"
    )
    assert "Bangalore" not in text


def test_food_config_uses_its_own_fields() -> None:
    dish = {
        "external_id": "dish-1",
        "name": "Paneer Tikka",
        "description": "Smoky grilled cottage cheese",
        "cuisine": "North Indian",
        "ingredients": ["paneer", "yogurt", "spices"],
        "price_range": "$$",
    }

    text = builder.build_embedding_text(dish, FOOD_CONFIG)

    assert text == (
        "description: Smoky grilled cottage cheese "
        "name: Paneer Tikka "
        "cuisine: North Indian "
        "ingredients: paneer yogurt spices"
    )


def test_missing_and_empty_fields_are_skipped_and_whitespace_collapsed() -> None:
    job = {"description": "  Remote\n\n role  ", "title": "", "skills": [None, " "]}

    assert builder.build_embedding_text(job, HR_CONFIG) == "description: Remote role"


def test_non_string_values_are_stringified() -> None:
    config = {
        "primary_embedding_field": "summary",
        "searchable_fields": ["summary", "years", "meta"],
    }

    text = builder.build_embedding_text(
        {"summary": "Data role", "years": 5, "meta": {"b": 1, "a": 2}}, config
    )

    assert text == 'summary: Data role years: 5 meta: {"a": 2, "b": 1}'


def test_nothing_to_embed_returns_empty_string() -> None:
    assert builder.build_embedding_text({"location": "Pune"}, HR_CONFIG) == ""
