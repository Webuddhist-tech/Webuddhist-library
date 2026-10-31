# pylint: disable=redefined-outer-name,too-many-lines
"""Repository integration and span tests using the disposable Neo4j fixture."""

import pytest
from exceptions import DataNotFoundError, DataValidationError
from identifier import generate_id
from models.annotation import Page, PaginationInput, Span, Volume
from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.edition import EditionInput, EditionType
from models.enums import ContributorRole
from models.text import TextInput
from models.person import PersonInput


@pytest.mark.asyncio(loop_scope="session")
class TestDatabase:
    async def test_create_and_retrieve_person(self, test_database):
        """Test full person creation and retrieval cycle"""
        db = test_database

        # Create a person with Tibetan and English names
        person = PersonInput(
            name=LocalizedString({"bo": "རིན་ཆེན་སྡེ།", "en": "Rinchen De"}),
            alt_names=[LocalizedString({"bo": "རིན་ཆེན་སྡེ་བ།"}), LocalizedString({"en": "Rinchen Dewa"})],
        )

        # Create in database
        person_id = await db.person.create(person)
        assert person_id is not None
        assert len(person_id) == 21  # NanoID length

        # Retrieve the person
        retrieved_person = await db.person.get(person_id)
        assert retrieved_person.id == person_id

        # Compare LocalizedString objects properly
        assert retrieved_person.name.root == {"bo": "རིན་ཆེན་སྡེ།", "en": "Rinchen De"}
        assert len(retrieved_person.alt_names) == 2

        # Check alt_names content regardless of order
        alt_names_roots = [alt_name.root for alt_name in retrieved_person.alt_names]
        assert {"bo": "རིན་ཆེན་སྡེ་བ།"} in alt_names_roots
        assert {"en": "Rinchen Dewa"} in alt_names_roots


    async def test_get_all_persons(self, test_database):
        """Test retrieving all persons"""
        db = test_database

        person1 = PersonInput(name=LocalizedString({"en": "John Doe"}))
        person2 = PersonInput(name=LocalizedString({"bo": "རིན་ཆེན་སྡེ།"}))
        person3 = PersonInput(name=LocalizedString({"sa": "मञ्जुश्री", "en": "Manjushri"}))

        id1 = await db.person.create(person1)
        id2 = await db.person.create(person2)
        id3 = await db.person.create(person3)

        # Retrieve all
        all_persons = await db.person.get_all()
        assert len(all_persons) == 3

        # Check that our created persons are in the results
        person_ids = [p.id for p in all_persons]
        assert id1 in person_ids
        assert id2 in person_ids
        assert id3 in person_ids


    async def test_person_not_found(self, test_database):
        """Test retrieving non-existent person"""
        db = test_database

        with pytest.raises(DataNotFoundError, match="Person with ID 'nonexistent' not found"):
            await db.person.get("nonexistent")


    async def test_person_with_bdrc_and_wiki_fields(self, test_database):
        """Test person creation and retrieval"""
        db = test_database

        # Create a person with bdrc and wiki values using the actual API
        person = PersonInput(
            bdrc="P123456",
            wiki="W123456",
            name=LocalizedString({"en": "Test Person", "bo": "བསྟན་པ་མི་"}),
            alt_names=[LocalizedString({"en": "Alternative Name"})],
        )

        # Create person using the actual create_person_neo4j method
        person_id = await db.person.create(person)
        assert person_id is not None
        assert len(person_id) == 21  # NanoID length

        # Retrieve the person and validate ALL fields
        retrieved_person = await db.person.get(person_id)

        # Validate that bdrc and wiki are properly stored and retrieved
        assert retrieved_person.id == person_id
        assert retrieved_person.bdrc == "P123456"
        assert retrieved_person.wiki == "W123456"
        assert retrieved_person.name.root == {"en": "Test Person", "bo": "བསྟན་པ་མི་"}
        assert len(retrieved_person.alt_names) == 1
        assert retrieved_person.alt_names[0].root == {"en": "Alternative Name"}  # Still empty


    async def test_texts_query_structure(self, test_database):
        """Test texts query with empty database"""
        db = test_database

        # Test texts query with no data
        result = await db.text.get_all(offset=0, limit=10, filters=None)
        assert isinstance(result, list)
        assert len(result) == 0  # Empty database


    async def test_text_not_found(self, test_database):
        """Test retrieving non-existent text"""
        db = test_database

        with pytest.raises(DataNotFoundError, match="Text with ID 'nonexistent' not found"):
            await db.text.get("nonexistent")


    async def test_edition_not_found(self, test_database):
        """Test retrieving editions for non-existent text"""
        # Test that getting editions for non-existent text returns empty list
        editions = await test_database.edition.get_all("nonexistent-text-id")
        assert isinstance(editions, list)
        assert len(editions) == 0




    async def test_create_root_text_success(self, test_database):
        """Test successful creation of ROOT type text with all components"""

        # First create a person to reference in contributions
        person = PersonInput(
            bdrc="P123456",
            wiki="Q123456",
            name=LocalizedString({"en": "Test Author", "bo": "རྩོམ་པ་པོ་"}),
            alt_names=[LocalizedString({"en": "Alternative Author Name"})],
        )
        person_id = await test_database.person.create(person)

        # Create ROOT text (no commentary_of or translation_of)
        text = TextInput(
            category_id="category",
            bdrc="W789012",
            wiki="Q789012",
            date="2024-01-15",
            title=LocalizedString({"bo": "དམ་པའི་ཆོས་པདྨ་དཀར་པོ།", "en": "The Sacred White Lotus Dharma"}),
            alt_titles=[LocalizedString({"bo": "པདྨ་དཀར་པོའི་མདོ།", "en": "White Lotus Sutra"})],
            language="bo",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )

        # Create the text
        text_id = await test_database.text.create(text)

        # Verify the text was created
        assert text_id is not None
        assert len(text_id) > 0

        # Verify we can retrieve the text
        retrieved_text = await test_database.text.get(text_id)
        assert retrieved_text.id == text_id
        assert retrieved_text.bdrc == "W789012"
        assert retrieved_text.wiki == "Q789012"
        assert retrieved_text.date == "2024-01-15"
        assert retrieved_text.language == "bo"

        # Verify title
        assert "bo" in retrieved_text.title.root
        assert "en" in retrieved_text.title.root
        assert retrieved_text.title.root["bo"] == "དམ་པའི་ཆོས་པདྨ་དཀར་པོ།"

        # Verify alt_titles
        assert retrieved_text.alt_titles is not None
        assert len(retrieved_text.alt_titles) == 1
        assert "bo" in retrieved_text.alt_titles[0].root
        assert retrieved_text.alt_titles[0].root["bo"] == "པདྨ་དཀར་པོའི་མདོ།"

        # Verify contributions
        assert len(retrieved_text.contributions) == 1
        contribution = retrieved_text.contributions[0]
        assert contribution.type == "person"
        assert contribution.id == person_id
        assert contribution.role == ContributorRole.AUTHOR


    async def test_create_root_text_missing_person(self, test_database):
        """Test that creating text with non-existent person fails and rolls back"""

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id="non-existent-person-id", role=ContributorRole.AUTHOR)],
        )

        # Should raise DataValidationError for missing person
        with pytest.raises(DataValidationError) as exc_info:
            await test_database.text.create(text)

        assert "non-existent-person-id" in str(exc_info.value)


    async def test_create_root_text_language_support(self, test_database):
        """Test that various language codes including BCP47 variants are properly supported"""

        # Create a person
        person = PersonInput(
            name=LocalizedString({"en": "Test Person"}),
        )
        person_id = await test_database.person.create(person)

        # Test various language inputs - BCP47 tags should be preserved
        test_cases = [
            ("bo-Latn", {"bo-Latn": "བོད་སྐད།", "en": "Tibetan Text Latin"}),
            ("zh-Hans-CN", {"zh-Hans-CN": "中文", "en": "Chinese Text"}),
            ("en-US", {"en-US": "American English"}),
            ("bo", {"bo": "བོད་སྐད།", "en": "Tibetan Text"}),
        ]

        for input_lang, title_dict in test_cases:
            text = TextInput(
                category_id="category",
                title=LocalizedString(title_dict),
                language=input_lang,
                contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
            )

            # Should create successfully
            text_id = await test_database.text.create(text)
            assert text_id is not None

            # Verify BCP47 language code is preserved
            retrieved = await test_database.text.get(text_id)
            assert retrieved.language == input_lang


    async def test_create_root_text_multiple_contributions(self, test_database):
        """Test creating text with multiple contributors"""
        # Create multiple persons
        author = PersonInput(
            name=LocalizedString({"en": "Primary Author"}),
        )
        author_id = await test_database.person.create(author)

        reviser = PersonInput(
            name=LocalizedString({"en": "Reviser"}),
        )
        reviser_id = await test_database.person.create(reviser)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Multi-Contributor Work"}),
            language="en",
            contributions=[
                PersonContributionInput(type="person", id=author_id, role=ContributorRole.AUTHOR),
                PersonContributionInput(type="person", id=reviser_id, role=ContributorRole.REVISER),
            ],
        )

        text_id = await test_database.text.create(text)
        retrieved = await test_database.text.get(text_id)

        # Verify both contributions
        assert len(retrieved.contributions) == 2

        # Check that we have both roles
        roles = {contrib.role for contrib in retrieved.contributions}
        assert ContributorRole.AUTHOR in roles
        assert ContributorRole.REVISER in roles

        # Check that we have both persons
        person_ids = {contrib.id for contrib in retrieved.contributions}
        assert author_id in person_ids
        assert reviser_id in person_ids


    async def test_create_root_text_minimal_data(self, test_database):
        """Test creating text with minimal required data"""
        # Create a person
        person = PersonInput(
            name=LocalizedString({"en": "Minimal Person"}),
        )
        person_id = await test_database.person.create(person)

        # Minimal text (no bdrc, wiki, date, alt_titles)
        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Minimal text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )

        text_id = await test_database.text.create(text)
        retrieved = await test_database.text.get(text_id)

        assert retrieved.id == text_id
        assert retrieved.bdrc is None
        assert retrieved.wiki is None
        assert retrieved.date is None
        assert retrieved.alt_titles is None or len(retrieved.alt_titles) == 0
        assert retrieved.title.root["en"] == "Minimal text"
        assert len(retrieved.contributions) == 1


    async def test_create_root_text_with_bdrc_id(self, test_database):
        """Test creating text with a person contribution using bdrc_id instead of id."""
        # Create a person with BDRC ID
        person = PersonInput(
            name=LocalizedString({"en": "BDRC Person", "bo": "བདྲ་ཅ་མི་སྣ།"}),
            bdrc="P123456",  # This is the BDRC ID we'll use for lookup
        )
        person_id = await test_database.person.create(person)

        # Create text using bdrc_id instead of id
        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "text with BDRC Contributor"}),
            language="en",
            contributions=[
                PersonContributionInput(
                    type="person",
                    bdrc_id="P123456",
                    role=ContributorRole.AUTHOR,
                )
            ],
        )

        # Should successfully create text using BDRC ID
        text_id = await test_database.text.create(text)
        retrieved = await test_database.text.get(text_id)

        # Verify the text was created correctly
        assert retrieved.id == text_id
        assert retrieved.title.root["en"] == "text with BDRC Contributor"
        assert len(retrieved.contributions) == 1

        # Verify the contribution is linked to the correct person
        contribution = retrieved.contributions[0]
        assert contribution.type == "person"
        assert contribution.id == person_id  # Should resolve to the actual person id
        assert contribution.bdrc_id == "P123456"  # Should also include the BDRC ID
        assert contribution.role == ContributorRole.AUTHOR


    async def test_create_root_text_missing_person_bdrc_id(self, test_database):
        """Test that creating text with non-existent person bdrc_id fails."""
        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[
                PersonContributionInput(type="person", bdrc_id="P999999", role=ContributorRole.AUTHOR)
            ],
        )

        # Should raise DataValidationError for missing person
        with pytest.raises(DataValidationError) as exc_info:
            await test_database.text.create(text)

        assert "P999999" in str(exc_info.value)


    async def test_create_translation_text_success(self, test_database):
        """Test creating a translation text that links to parent"""
        # First create a root text (parent)
        person = PersonInput(
            name=LocalizedString({"en": "Original Author"}),
        )
        person_id = await test_database.person.create(person)

        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Original Text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        root_text_id = await test_database.text.create(root_text)

        # Create translator person
        translator = PersonInput(
            name=LocalizedString({"en": "Translator Name"}),
        )
        translator_id = await test_database.person.create(translator)

        # Now create a translation text using translation_of
        translation_text = TextInput(

            title=LocalizedString({"bo": "བསྒྱུར་བ།"}),
            language="bo",
            translation_of=root_text_id,
            contributions=[PersonContributionInput(type="person", id=translator_id, role=ContributorRole.TRANSLATOR)],
        )

        translation_id = await test_database.text.create(translation_text)
        retrieved = await test_database.text.get(translation_id)

        # Verify the translation was created correctly
        assert retrieved.id == translation_id
        assert retrieved.translation_of == root_text_id
        assert retrieved.title.root["bo"] == "བསྒྱུར་བ།"
        assert retrieved.language == "bo"
        assert len(retrieved.contributions) == 1
        assert retrieved.contributions[0].role == ContributorRole.TRANSLATOR


    async def test_create_text_both_commentary_and_translation_fails(self, test_database):
        """Test that creating text with both commentary_of and translation_of fails validation"""
        # Create a person for the contribution
        person = PersonInput(
            name=LocalizedString({"en": "Author"}),
        )
        person_id = await test_database.person.create(person)

        # Create a root text first
        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Root Text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        root_id = await test_database.text.create(root_text)

        # Try to create text with both commentary_of and translation_of - should fail validation
        with pytest.raises(ValueError, match="Cannot be both a commentary and translation"):
            TextInput(

                title=LocalizedString({"bo": "བསྒྱུར་བ།"}),
                language="bo",
                commentary_of=root_id,
                translation_of=root_id,
                contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
            )


    async def test_create_translation_text_nonexistent_target(self, test_database):
        """Test that creating translation with non-existent target fails"""
        # Create a person for the contribution
        person = PersonInput(
            name=LocalizedString({"en": "Translator"}),
        )
        person_id = await test_database.person.create(person)

        # Create translation with non-existent target
        translation_text = TextInput(

            title=LocalizedString({"bo": "བསྒྱུར་བ།"}),
            language="bo",
            translation_of="nonexistent-target-id",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )

        # Should fail when trying to create in database
        with pytest.raises(DataNotFoundError, match="nonexistent-target-id"):
            await test_database.text.create(translation_text)


    async def test_create_commentary_text_success(self, test_database):
        """Test creating a commentary text using commentary_of"""
        # First create a root text (parent)
        person = PersonInput(
            name=LocalizedString({"en": "Original Author"}),
        )
        person_id = await test_database.person.create(person)

        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Original Text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        root_text_id = await test_database.text.create(root_text)

        # Create commentator person
        commentator = PersonInput(
            name=LocalizedString({"en": "Commentator Name"}),
        )
        commentator_id = await test_database.person.create(commentator)

        # Now create a commentary text using commentary_of
        commentary_text = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།"}),
            language="bo",
            commentary_of=root_text_id,
            contributions=[PersonContributionInput(type="person", id=commentator_id, role=ContributorRole.AUTHOR)],
        )

        commentary_id = await test_database.text.create(commentary_text)
        retrieved = await test_database.text.get(commentary_id)

        # Verify the commentary was created correctly
        assert retrieved.id == commentary_id
        assert retrieved.commentary_of == root_text_id
        assert retrieved.bdrc is None
        assert retrieved.wiki is None
        assert retrieved.date is None
        assert retrieved.title.root["bo"] == "འགྲེལ་པ།"
        assert retrieved.language == "bo"
        assert len(retrieved.contributions) == 1
        assert retrieved.contributions[0].type == "person"
        assert retrieved.contributions[0].id == commentator_id
        assert retrieved.contributions[0].role == ContributorRole.AUTHOR


    async def test_create_commentary_text_nonexistent_target(self, test_database):
        """Test that creating commentary with non-existent target fails"""
        # Create a person for the contribution
        person = PersonInput(
            name=LocalizedString({"en": "Commentator"}),
        )
        person_id = await test_database.person.create(person)

        # Create commentary text with non-existent target
        commentary_text = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།"}),
            language="bo",
            commentary_of="nonexistent-target-id",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )

        # Should fail when trying to create in database
        with pytest.raises(DataNotFoundError, match="nonexistent-target-id"):
            await test_database.text.create(commentary_text)


    async def test_create_commentary_text_with_multiple_contributions(self, test_database):
        """Test creating commentary text with multiple contributors"""
        # Create target text
        author = PersonInput(
            name=LocalizedString({"en": "Original Author"}),
        )
        author_id = await test_database.person.create(author)

        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Original Text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=author_id, role=ContributorRole.AUTHOR)],
        )
        root_text_id = await test_database.text.create(root_text)

        # Create multiple contributors for commentary
        commentator = PersonInput(
            name=LocalizedString({"en": "Commentator"}),
        )
        commentator_id = await test_database.person.create(commentator)

        reviser = PersonInput(
            name=LocalizedString({"en": "Reviser"}),
        )
        reviser_id = await test_database.person.create(reviser)

        # Create commentary with multiple contributions
        commentary_text = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།"}),
            language="bo",
            commentary_of=root_text_id,
            contributions=[
                PersonContributionInput(type="person", id=commentator_id, role=ContributorRole.AUTHOR),
                PersonContributionInput(type="person", id=reviser_id, role=ContributorRole.REVISER),
            ],
        )

        commentary_id = await test_database.text.create(commentary_text)
        retrieved = await test_database.text.get(commentary_id)

        # Verify multiple contributions
        assert len(retrieved.contributions) == 2
        contribution_roles = {contrib.role for contrib in retrieved.contributions}
        assert ContributorRole.AUTHOR in contribution_roles
        assert ContributorRole.REVISER in contribution_roles

    # Editions Tests

    async def test_get_editions_by_text_empty(self, test_database):
        """Test getting editions for text with no editions."""
        # Create a basic text first
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        # Get editions for text with no editions
        editions = await test_database.edition.get_all(text_id)
        assert editions == []


    async def test_get_editions_by_text_with_different_types(self, test_database):
        """Test getting editions for different text types (ROOT, TRANSLATION, COMMENTARY)."""
        # Create person
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        # Test ROOT text
        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Root text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        root_id = await test_database.text.create(root_text)

        # Test TRANSLATION text
        translation_text = TextInput(

            title=LocalizedString({"bo": "འགྱུར་བ།"}),
            language="bo",
            translation_of=root_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )
        translation_id = await test_database.text.create(translation_text)

        # Test COMMENTARY text
        commentary_text = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།"}),
            language="bo",
            commentary_of=root_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        commentary_id = await test_database.text.create(commentary_text)

        from models.edition import EditionInput
        from identifier import generate_id
        expected = {}
        for text_id in (root_id, translation_id, commentary_id):
            edition_id = generate_id()
            await test_database.edition.create(EditionInput(type="critical"), edition_id, text_id, 10)
            expected[text_id] = edition_id
        for text_id, edition_id in expected.items():
            editions = await test_database.edition.get_all(text_id)
            assert [edition.id for edition in editions] == [edition_id]


    async def test_create_edition_basic(self, test_database):
        """Test creating a basic edition."""
        # Create text first
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        edition = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source",
            colophon="Test colophon",
        )

        # Create edition in database
        edition_id = generate_id()
        await test_database.edition.create(edition, edition_id, text_id, content_length=10)

        # Verify we can get raw editions list
        retrieved_editions = await test_database.edition.get_all(text_id)
        assert isinstance(retrieved_editions, list)
        assert len(retrieved_editions) == 1
        assert retrieved_editions[0].id == edition_id


    async def test_create_multiple_editions_for_text(self, test_database):
        """Test creating multiple editions for the same text."""
        # Create text first
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        # Create first edition (CRITICAL)
        edition1 = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source 1",
            colophon="First edition",
        )
        edition1_id = generate_id()
        await test_database.edition.create(edition1, edition1_id, text_id, content_length=10)

        # Create second edition (DIPLOMATIC - requires bdrc)
        edition2 = EditionInput(
            type=EditionType.DIPLOMATIC,
            bdrc="W12345",
            source="Test Source 2",
            colophon="Second edition",
        )
        edition2_id = generate_id()
        edition2_pagination = PaginationInput(
            volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])]
        )
        await test_database.edition.create(
            edition2, edition2_id, text_id, content_length=10, pagination=edition2_pagination
        )

        # Retrieve all editions
        retrieved_editions = await test_database.edition.get_all(text_id)
        assert len(retrieved_editions) == 2

        # Verify both editions are present
        retrieved_ids = {m.id for m in retrieved_editions}
        assert edition1_id in retrieved_ids
        assert edition2_id in retrieved_ids

        # Verify types
        retrieved_types = {m.type for m in retrieved_editions}
        assert EditionType.CRITICAL in retrieved_types
        assert EditionType.DIPLOMATIC in retrieved_types


    async def test_create_edition_nonexistent_text(self, test_database):
        """Test that creating edition for non-existent text fails."""
        edition = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source",
        )

        # Should raise DataValidationError for non-existent text
        with pytest.raises(DataValidationError, match="Text nonexistent-id does not exist"):
            await test_database.edition.create(edition, generate_id(), "nonexistent-id", content_length=10)


    async def test_create_edition_with_source(self, test_database):
        """Test that source field is stored in Source node and retrieved correctly."""
        # Create text first
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        # Create edition with source
        edition = EditionInput(
            type=EditionType.CRITICAL,
            source="BDRC Library",
            colophon="Test colophon",
        )
        edition_id = generate_id()
        await test_database.edition.create(edition, edition_id, text_id, content_length=10)

        # Retrieve and verify source is returned
        retrieved = await test_database.edition.get(edition_id)
        assert retrieved.id == edition_id
        assert retrieved.source == "BDRC Library"
        assert retrieved.colophon == "Test colophon"
        assert retrieved.type == EditionType.CRITICAL


    async def test_create_edition_without_source(self, test_database):
        """Test that edition without source works correctly."""
        # Create text first
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        # Create edition without source
        edition = EditionInput(
            type=EditionType.CRITICAL,
        )
        edition_id = generate_id()
        await test_database.edition.create(edition, edition_id, text_id, content_length=10)

        # Retrieve and verify source is None
        retrieved = await test_database.edition.get(edition_id)
        assert retrieved.id == edition_id
        assert retrieved.source is None
        assert retrieved.type == EditionType.CRITICAL


    async def test_create_edition_with_text(self, test_database):
        """Test creating edition with text in same transaction."""
        # Create a person for the text contribution
        person = PersonInput(
            name=LocalizedString({"en": "Test Author"}),
        )
        person_id = await test_database.person.create(person)

        # Create text input (not yet in database)
        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "New text Created With Edition"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )

        # Create edition input
        edition = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source",
            colophon="Test colophon",
        )

        # Create both in same transaction
        edition_id = generate_id()
        text_id = generate_id()
        await test_database.edition.create(
            edition, edition_id, text_id, content_length=10, text=text
        )

        # Verify text was created
        retrieved_text = await test_database.text.get(text_id)
        assert retrieved_text.id == text_id
        assert retrieved_text.title.root["en"] == "New text Created With Edition"
        assert len(retrieved_text.contributions) == 1

        # Verify edition was created and linked
        retrieved_edition = await test_database.edition.get(edition_id)
        assert retrieved_edition.id == edition_id
        assert retrieved_edition.source == "Test Source"
        assert retrieved_edition.type == EditionType.CRITICAL

        # Verify edition is linked to text
        editions = await test_database.edition.get_all(text_id)
        assert len(editions) == 1
        assert editions[0].id == edition_id


    async def test_create_edition_with_text_rollback_on_invalid_person(self, test_database):
        """Test that transaction rolls back if text creation fails due to invalid person."""
        # Create text with non-existent person
        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "text With Invalid Person"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id="nonexistent-person-id", role=ContributorRole.AUTHOR)],
        )

        edition = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source",
        )

        edition_id = generate_id()
        text_id = generate_id()

        # Should fail due to invalid person in text
        with pytest.raises(DataValidationError, match="nonexistent-person-id"):
            await test_database.edition.create(
                edition, edition_id, text_id, content_length=10, text=text
            )

        # Verify nothing was created (transaction rolled back)
        with pytest.raises(DataNotFoundError):
            await test_database.text.get(text_id)

        with pytest.raises(DataNotFoundError):
            await test_database.edition.get(edition_id)


    async def test_create_edition_with_translation_text(self, test_database):
        """Test creating edition with translation text in same transaction."""
        # Create person
        person = PersonInput(
            name=LocalizedString({"en": "Author"}),
        )
        person_id = await test_database.person.create(person)

        # Create root text first (parent)
        root_text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Root Text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        root_text_id = await test_database.text.create(root_text)

        # Create translation text input
        translation_text = TextInput(

            title=LocalizedString({"bo": "བསྒྱུར་བ།"}),
            language="bo",
            translation_of=root_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )

        edition = EditionInput(
            type=EditionType.DIPLOMATIC,
            bdrc="W12345",
        )

        # Create both in same transaction
        edition_id = generate_id()
        translation_id = generate_id()
        pagination = PaginationInput(
            volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])]
        )
        await test_database.edition.create(
            edition, edition_id, translation_id, content_length=10, text=translation_text, pagination=pagination
        )

        # Verify translation text was created with parent link
        retrieved_text = await test_database.text.get(translation_id)
        assert retrieved_text.id == translation_id
        assert retrieved_text.translation_of == root_text_id
        assert retrieved_text.language == "bo"

        # Verify edition was created
        retrieved_edition = await test_database.edition.get(edition_id)
        assert retrieved_edition.id == edition_id
        assert retrieved_edition.type == EditionType.DIPLOMATIC


@pytest.mark.asyncio(loop_scope="session")
class TestSpanDatabase:
    """Tests for SpanDatabase span adjustment functionality."""

    async def _create_test_setup(self, test_database):
        """Helper to create text, edition, segmentation, and segment for testing."""
        person = PersonInput(name=LocalizedString({"en": "Test Author"}))
        person_id = await test_database.person.create(person)

        text = TextInput(
            category_id="category",
            title=LocalizedString({"en": "Test text"}),
            language="en",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        text_id = await test_database.text.create(text)

        edition = EditionInput(type=EditionType.CRITICAL)
        edition_id = generate_id()
        await test_database.edition.create(edition, edition_id, text_id, content_length=10)

        segment_id = generate_id()
        segmentation_id = generate_id()
        async with test_database.get_session() as session:
            await session.run(
                """
                MATCH (m:Edition {id: $edition_id})
                CREATE (m)-[:HAS_SEGMENTATION]->(segmentation:Segmentation {id: $segmentation_id})
                CREATE (seg:Segment {id: $segment_id})-[:SEGMENT_OF]->(segmentation)
                CREATE (span:Span {start: $start, end: $end})-[:SPAN_OF]->(seg)
                """,
                edition_id=edition_id,
                segmentation_id=segmentation_id,
                segment_id=segment_id,
                start=0,
                end=12,
            )

        return {
            "text_id": text_id,
            "edition_id": edition_id,
            "segment_id": segment_id,
            "segmentation_id": segmentation_id,
        }

    async def _add_note(self, test_database, edition_id: str, start: int, end: int) -> str:
        """Helper to add a note with a span."""
        note_id = generate_id()
        async with test_database.get_session() as session:
            await session.run(
                """
                MATCH (m:Edition {id: $edition_id}), (nt:NoteType {name: 'durchen'})
                CREATE (span:Span {start: $start, end: $end})-[:SPAN_OF]->(n:Note {id: $note_id, text: 'test'})
                CREATE (n)-[:NOTE_OF]->(m)
                CREATE (n)-[:HAS_TYPE]->(nt)
                """,
                edition_id=edition_id,
                note_id=note_id,
                start=start,
                end=end,
            )
        return note_id

    async def _add_second_segment(self, test_database, edition_id: str, start: int, end: int) -> str:
        """Helper to add a second segment."""
        segment_id = generate_id()
        async with test_database.get_session() as session:
            await session.run(
                """
                MATCH (m:Edition {id: $edition_id})-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
                CREATE (seg:Segment {id: $segment_id})-[:SEGMENT_OF]->(segmentation)
                CREATE (span:Span {start: $start, end: $end})-[:SPAN_OF]->(seg)
                """,
                edition_id=edition_id,
                segment_id=segment_id,
                start=start,
                end=end,
            )
        return segment_id

    async def _get_span(self, test_database, entity_id: str) -> tuple[int, int] | None:
        """Helper to get span for any entity."""
        async with test_database.get_session() as session:
            result = await session.run(
                "MATCH (s:Span)-[:SPAN_OF]->(e {id: $id}) RETURN s.start AS start, s.end AS end",
                id=entity_id,
            )
            record = await result.single()
            if record is None:
                return None
            return (record["start"], record["end"])


@pytest.mark.asyncio(loop_scope="session")
class TestSpanAdjustmentFunctions:
    """Unit tests for the new span adjustment helper functions."""

    async def test_continuous_insert_at_position_zero_expands_first_segment(self):
        """Insert at position 0 should expand first continuous span (start=0)."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=0, end=10, insert_pos=0, insert_len=5)
        assert result == (0, 15)

    async def test_continuous_insert_at_position_zero_shifts_non_first_segment(self):
        """Insert at position 0 should shift continuous spans that don't start at 0."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=5, end=15, insert_pos=0, insert_len=5)
        assert result == (10, 20)

    async def test_continuous_insert_at_start_boundary_shifts(self):
        """Insert at continuous span start boundary should shift it."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=10, insert_len=5)
        assert result == (15, 25)

    async def test_continuous_insert_at_end_boundary_expands(self):
        """Insert at continuous span end boundary should expand it."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=20, insert_len=5)
        assert result == (10, 25)

    async def test_continuous_insert_inside_expands(self):
        """Insert inside continuous span should expand it."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=15, insert_len=5)
        assert result == (10, 25)

    async def test_continuous_insert_after_unchanged(self):
        """Insert after continuous span should leave it unchanged."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=25, insert_len=5)
        assert result == (10, 20)

    async def test_annotation_insert_at_start_boundary_shifts(self):
        """Insert at annotation start boundary should shift it."""
        from database.span_database import _adjust_annotation_for_insert

        result = _adjust_annotation_for_insert(start=10, end=20, insert_pos=10, insert_len=5)
        assert result == (15, 25)

    async def test_annotation_insert_at_end_boundary_unchanged(self):
        """Insert at annotation end boundary should leave it unchanged."""
        from database.span_database import _adjust_annotation_for_insert

        result = _adjust_annotation_for_insert(start=10, end=20, insert_pos=20, insert_len=5)
        assert result == (10, 20)

    async def test_annotation_insert_inside_expands(self):
        """Insert strictly inside annotation should expand it."""
        from database.span_database import _adjust_annotation_for_insert

        result = _adjust_annotation_for_insert(start=10, end=20, insert_pos=15, insert_len=5)
        assert result == (10, 25)

    async def test_delete_fully_encompasses_returns_none(self):
        """Delete that fully encompasses span should return None."""
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=10, end=20, del_start=5, del_end=25)
        assert result is None

    async def test_delete_before_shifts_left(self):
        """Delete before span should shift it left."""
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=20, end=30, del_start=5, del_end=10)
        assert result == (15, 25)

    async def test_delete_after_unchanged(self):
        """Delete after span should leave it unchanged."""
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=10, end=20, del_start=25, del_end=30)
        assert result == (10, 20)

    async def test_delete_overlaps_start_trims(self):
        """Delete overlapping start should trim the span and shift."""
        from database.span_database import _adjust_span_for_delete

        # Span (10,20), delete [5,15): del_start <= start < del_end < end
        # Result: (del_start, end - del_len) = (5, 20 - 10) = (5, 10)
        result = _adjust_span_for_delete(start=10, end=20, del_start=5, del_end=15)
        assert result == (5, 10)

    async def test_delete_overlaps_end_trims(self):
        """Delete overlapping end should trim the span."""
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=10, end=20, del_start=15, del_end=25)
        assert result == (10, 15)

    async def test_delete_inside_shrinks(self):
        """Delete inside span should shrink it."""
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=10, end=30, del_start=15, del_end=20)
        assert result == (10, 25)

    @pytest.mark.parametrize("is_page", [False, True])
    @pytest.mark.parametrize(
        ("lines", "start", "end", "new_len", "expected"),
        [
            ([("text", 10, 20)], 10, 20, 15, {"text": (10, 25)}),
            ([("text", 4, 9)], 4, 9, 5, {"text": (4, 9)}),
            ([("text", 4, 9)], 4, 9, 2, {"text": (4, 6)}),
            ([("text", 10, 20)], 5, 25, 10, {"text": (5, 15)}),
            ([("text", 0, 10)], 0, 7, 2, {"text": (0, 5)}),
            ([("first", 0, 5), ("last", 5, 10)], 1, 10, 2, {"first": (0, 3)}),
            ([("first", 0, 5), ("marker", 5, 5), ("last", 5, 10)], 6, 7, 1,
             {"first": (0, 5), "marker": (5, 5), "last": (5, 10)}),
            ([("marker", 1, 1), ("text", 1, 3)], 0, 4, 2, {"marker": (0, 0), "text": (0, 2)}),
            ([("first", 4, 6), ("last", 6, 8)], 2, 10, 3, {"first": (2, 5)}),
        ],
    )
    async def test_continuous_replacement_preserves_lines_and_markers(self, is_page, lines, start, end, new_len, expected):
        from database.span_database import _adjust_continuous_entities

        assert _adjust_continuous_entities({("collection", "entity", is_page): lines}, start, end, new_len) == expected

    async def test_annotation_replace_exact_match_deletes(self):
        """Replace exact match should delete annotation."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=20, replace_start=10, replace_end=20, new_len=15)
        assert result is None

    async def test_annotation_replace_encompasses_deletes(self):
        """Replace encompassing annotation should delete it."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=20, replace_start=5, replace_end=25, new_len=10)
        assert result is None

    async def test_replace_partial_overlap_start_trims(self):
        """Replace overlapping start should trim the span."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=20, replace_start=5, replace_end=15, new_len=3)
        assert result == (8, 13)

    async def test_replace_partial_overlap_end_trims(self):
        """Replace overlapping end should trim the span."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=20, replace_start=15, replace_end=25, new_len=3)
        assert result == (10, 18)

    async def test_replace_inside_span_adjusts(self):
        """Replace inside span should adjust its size."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=30, replace_start=15, replace_end=20, new_len=3)
        assert result == (10, 28)


@pytest.mark.asyncio(loop_scope="session")
class TestSpanAdjustmentEdgeCases:
    """Edge case tests from text-editing-edge-cases document."""

    async def test_insert_at_boundary_between_adjacent_continuous_spans(self):
        """Insert at boundary between two adjacent continuous spans should not cause overlap.

        Setup: S1 [0, 10), S2 [10, 20)
        Operation: Insert at position 10
        Expected: S1 expands to [0, 15), S2 shifts to [15, 25) - no overlap
        """
        from database.span_database import _adjust_continuous_for_insert

        s1_result = _adjust_continuous_for_insert(start=0, end=10, insert_pos=10, insert_len=5)
        s2_result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=10, insert_len=5)

        assert s1_result == (0, 15)
        assert s2_result == (15, 25)
        assert s1_result[1] == s2_result[0]

    async def test_delete_exact_match_returns_none(self):
        """Delete that exactly matches span should return None.

        Operation: Delete [4, 9) on span [4, 9)
        """
        from database.span_database import _adjust_span_for_delete

        result = _adjust_span_for_delete(start=4, end=9, del_start=4, del_end=9)
        assert result is None

    async def test_delete_across_multiple_spans(self):
        """Delete crossing multiple spans should trim both.

        Setup: B2 [4, 9), B3 [9, 15)
        Operation: Delete [7, 12) - crosses both spans
        Expected: B2 trims to [4, 7), B3 trims and shifts to [7, 10)
        """
        from database.span_database import _adjust_span_for_delete

        b2_result = _adjust_span_for_delete(start=4, end=9, del_start=7, del_end=12)
        b3_result = _adjust_span_for_delete(start=9, end=15, del_start=7, del_end=12)

        assert b2_result == (4, 7)
        assert b3_result == (7, 10)

    async def test_delete_creates_continuous_segmentation(self):
        """Delete entire segment should maintain continuity.

        Setup: S1 [0, 10), S2 [10, 20), S3 [20, 30)
        Operation: Delete [10, 20) (exactly S2)
        Expected: S1 stays [0, 10), S2 deleted, S3 shifts to [10, 20)
        """
        from database.span_database import _adjust_span_for_delete

        s1_result = _adjust_span_for_delete(start=0, end=10, del_start=10, del_end=20)
        s2_result = _adjust_span_for_delete(start=10, end=20, del_start=10, del_end=20)
        s3_result = _adjust_span_for_delete(start=20, end=30, del_start=10, del_end=20)

        assert s1_result == (0, 10)
        assert s2_result is None
        assert s3_result == (10, 20)
        assert s1_result is not None and s3_result is not None
        assert s1_result[1] == s3_result[0]

    async def test_delete_partial_overlap_maintains_continuity(self):
        """Delete partial overlap should maintain continuity.

        Setup: S1 [0, 10), S2 [10, 20), S3 [20, 30)
        Operation: Delete [5, 15) (partial S1, partial S2)
        Expected: S1 [0, 5), S2 [5, 10), S3 [10, 20)
        """
        from database.span_database import _adjust_span_for_delete

        s1_result = _adjust_span_for_delete(start=0, end=10, del_start=5, del_end=15)
        s2_result = _adjust_span_for_delete(start=10, end=20, del_start=5, del_end=15)
        s3_result = _adjust_span_for_delete(start=20, end=30, del_start=5, del_end=15)

        assert s1_result == (0, 5)
        assert s2_result == (5, 10)
        assert s3_result == (10, 20)
        assert s1_result is not None and s2_result is not None and s3_result is not None
        assert s1_result[1] == s2_result[0]
        assert s2_result[1] == s3_result[0]


    async def test_independent_collections_choose_their_own_replacement_owner(self):
        from database.span_database import _adjust_continuous_entities

        entities = {
            ("segmentation", "a1", False): [("a1", 0, 20)],
            ("segmentation", "a2", False): [("a2", 20, 40)],
            ("pagination", "b1", True): [("b1", 0, 10)],
            ("pagination", "b2", True): [("b2", 10, 20)],
            ("pagination", "b3", True): [("b3", 20, 40)],
        }
        assert _adjust_continuous_entities(entities, 5, 15, 3) == {
            "a1": (0, 13), "a2": (13, 33), "b1": (0, 8), "b2": (8, 13), "b3": (13, 33),
        }

    async def test_overlapping_annotations_handled_correctly(self):
        """Overlapping annotations should be handled correctly.

        Setup: N1 [5, 15), N2 [10, 20)
        Operation: Delete [12, 18)
        Expected: N1 trims to [5, 12), N2 shrinks to [10, 14)
        """
        from database.span_database import _adjust_span_for_delete

        n1_result = _adjust_span_for_delete(start=5, end=15, del_start=12, del_end=18)
        n2_result = _adjust_span_for_delete(start=10, end=20, del_start=12, del_end=18)

        assert n1_result == (5, 12)
        assert n2_result == (10, 14)

    async def test_nested_spans_handled_correctly(self):
        """Nested spans should be handled correctly.

        Setup: Outer [0, 40), Inner [10, 20)
        Operation: Delete [5, 25)
        Expected: Outer shrinks to [0, 20), Inner deleted
        """
        from database.span_database import _adjust_span_for_delete

        outer_result = _adjust_span_for_delete(start=0, end=40, del_start=5, del_end=25)
        inner_result = _adjust_span_for_delete(start=10, end=20, del_start=5, del_end=25)

        assert outer_result == (0, 20)
        assert inner_result is None

    async def test_annotation_insert_before_shifts(self):
        """Insert before annotation should shift it."""
        from database.span_database import _adjust_annotation_for_insert

        result = _adjust_annotation_for_insert(start=10, end=20, insert_pos=5, insert_len=5)
        assert result == (15, 25)

    async def test_annotation_insert_after_unchanged(self):
        """Insert after annotation should leave it unchanged."""
        from database.span_database import _adjust_annotation_for_insert

        result = _adjust_annotation_for_insert(start=10, end=20, insert_pos=25, insert_len=5)
        assert result == (10, 20)

    async def test_continuous_insert_before_shifts(self):
        """Insert before continuous span should shift it."""
        from database.span_database import _adjust_continuous_for_insert

        result = _adjust_continuous_for_insert(start=10, end=20, insert_pos=5, insert_len=5)
        assert result == (15, 25)

    async def test_replace_before_shifts(self):
        """Replace before span should shift it by delta."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=20, end=30, replace_start=5, replace_end=10, new_len=3)
        assert result == (18, 28)

    async def test_replace_after_unchanged(self):
        """Replace after span should leave it unchanged."""
        from database.span_database import _adjust_annotation_for_replace

        result = _adjust_annotation_for_replace(start=10, end=20, replace_start=25, replace_end=30, new_len=3)
        assert result == (10, 20)
