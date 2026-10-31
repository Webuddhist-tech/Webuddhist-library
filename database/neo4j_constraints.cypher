// =============================================================================
// UNIQUE CONSTRAINTS FOR NODES WITH ID PROPERTIES
// =============================================================================

// Person nodes - each person must have a unique ID
CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE;

// Text nodes - each text must have a unique ID
CREATE CONSTRAINT text_id_unique IF NOT EXISTS FOR (e:Text) REQUIRE e.id IS UNIQUE;

// Work nodes - each work must have a unique ID
CREATE CONSTRAINT work_id_unique IF NOT EXISTS FOR (w:Work) REQUIRE w.id IS UNIQUE;

// Edition nodes - each edition must have a unique ID
CREATE CONSTRAINT edition_id_unique IF NOT EXISTS FOR (m:Edition) REQUIRE m.id IS UNIQUE;

// Segment nodes - each segment must have a unique ID
CREATE CONSTRAINT segment_id_unique IF NOT EXISTS FOR (s:Segment) REQUIRE s.id IS UNIQUE;

// Segmentation nodes - each segmentation must have a unique ID
CREATE CONSTRAINT segmentation_id_unique IF NOT EXISTS FOR (seg:Segmentation) REQUIRE seg.id IS UNIQUE;

// Pagination nodes - each pagination must have a unique ID
CREATE CONSTRAINT pagination_id_unique IF NOT EXISTS FOR (p:Pagination) REQUIRE p.id IS UNIQUE;

// TableOfContents nodes - each table of contents must have a unique ID
CREATE CONSTRAINT toc_id_unique IF NOT EXISTS FOR (toc:TableOfContents) REQUIRE toc.id IS UNIQUE;

// TableOfContentsSection nodes - each table of contents section must have a unique ID
CREATE CONSTRAINT toc_section_id_unique IF NOT EXISTS FOR (tocs:TableOfContentsSection) REQUIRE tocs.id IS UNIQUE;

// AnnotationMetadata nodes - each metadata node must have a unique ID
CREATE CONSTRAINT annotation_metadata_id_unique IF NOT EXISTS FOR (am:AnnotationMetadata) REQUIRE am.id IS UNIQUE;

// Note nodes - each note must have a unique ID
CREATE CONSTRAINT note_id_unique IF NOT EXISTS FOR (n:Note) REQUIRE n.id IS UNIQUE;

// Mark nodes - each mark must have a unique ID
CREATE CONSTRAINT mark_id_unique IF NOT EXISTS FOR (m:Mark) REQUIRE m.id IS UNIQUE;

// BibliographicMetadata nodes - each metadata item must have a unique ID
CREATE CONSTRAINT bibliographic_id_unique IF NOT EXISTS FOR (b:BibliographicMetadata) REQUIRE b.id IS UNIQUE;

// Attribute nodes - each attribute must have a unique ID
CREATE CONSTRAINT attribute_id_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE;

// Recording nodes - each recording must have a unique ID
CREATE CONSTRAINT recording_id_unique IF NOT EXISTS FOR (r:Recording) REQUIRE r.id IS UNIQUE;

// =============================================================================
// UNIQUE CONSTRAINTS FOR ENUM/LOOKUP NODES
// =============================================================================

// Language nodes - each language must have a unique code
CREATE CONSTRAINT language_code_unique IF NOT EXISTS FOR (l:Language) REQUIRE l.code IS UNIQUE;

// RoleType nodes - each role type must have a unique name
CREATE CONSTRAINT role_type_name_unique IF NOT EXISTS FOR (rt:RoleType) REQUIRE rt.name IS UNIQUE;

// EditionType nodes - each edition type must have a unique name
CREATE CONSTRAINT edition_type_name_unique IF NOT EXISTS FOR (mt:EditionType) REQUIRE mt.name IS UNIQUE;

// LicenseType nodes - each license type must have a unique name
CREATE CONSTRAINT license_type_name_unique IF NOT EXISTS FOR (lt:LicenseType) REQUIRE lt.name IS UNIQUE;

// MarkType nodes - each mark type must have a unique name
CREATE CONSTRAINT mark_type_name_unique IF NOT EXISTS FOR (mt:MarkType) REQUIRE mt.name IS UNIQUE;

// =============================================================================
// UNIQUE CONSTRAINTS FOR EXTERNAL IDENTIFIERS (BDRC/WIKI)
// =============================================================================

// Person nodes - BDRC IDs must be unique when present
CREATE CONSTRAINT person_bdrc_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.bdrc IS UNIQUE;

// Person nodes - Wiki IDs must be unique when present
CREATE CONSTRAINT person_wiki_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.wiki IS UNIQUE;

// Text nodes - BDRC IDs must be unique when present
CREATE CONSTRAINT text_bdrc_unique IF NOT EXISTS FOR (e:Text) REQUIRE e.bdrc IS UNIQUE;

// Text nodes - Wiki IDs must be unique when present
CREATE CONSTRAINT text_wiki_unique IF NOT EXISTS FOR (e:Text) REQUIRE e.wiki IS UNIQUE;

// Work nodes - BDRC IDs must be unique when present
CREATE CONSTRAINT work_bdrc_unique IF NOT EXISTS FOR (w:Work) REQUIRE w.bdrc IS UNIQUE;

// Work nodes - Wiki IDs must be unique when present
CREATE CONSTRAINT work_wiki_unique IF NOT EXISTS FOR (w:Work) REQUIRE w.wiki IS UNIQUE;

// Edition nodes - BDRC IDs must be unique when present
CREATE CONSTRAINT edition_bdrc_unique IF NOT EXISTS FOR (m:Edition) REQUIRE m.bdrc IS UNIQUE;

// Edition nodes - Wiki IDs must be unique when present
CREATE CONSTRAINT edition_wiki_unique IF NOT EXISTS FOR (m:Edition) REQUIRE m.wiki IS UNIQUE;

// Nomen nodes - each nomen must have a unique ID
CREATE CONSTRAINT nomen_id_unique IF NOT EXISTS FOR (n:Nomen) REQUIRE n.id IS UNIQUE;

// Category nodes - each category must have a unique ID
CREATE CONSTRAINT category_id_unique IF NOT EXISTS FOR (c:Category) REQUIRE c.id IS UNIQUE;

// Tag nodes - each tag must have a unique ID
CREATE CONSTRAINT tag_id_unique IF NOT EXISTS FOR (t:Tag) REQUIRE t.id IS UNIQUE;

// Application nodes - each application must have a unique ID (slug)
CREATE CONSTRAINT application_id_unique IF NOT EXISTS
FOR (a:Application) REQUIRE a.id IS UNIQUE;

// Index on Source name for MERGE lookups during edition creation
CREATE INDEX source_name_index IF NOT EXISTS FOR (s:Source) ON (s.name);

// Index on ApiKey id for faster lookups
CREATE CONSTRAINT api_key_id_unique IF NOT EXISTS FOR (ak:ApiKey) REQUIRE ak.id IS UNIQUE;

// ApiKey hash must be unique (used for validation on every API request)
CREATE CONSTRAINT api_key_hash_unique IF NOT EXISTS FOR (ak:ApiKey) REQUIRE ak.api_key_hash IS UNIQUE;
CREATE CONSTRAINT ai_id_unique IF NOT EXISTS FOR (n:AI) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT volume_id_unique IF NOT EXISTS FOR (n:Volume) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT page_id_unique IF NOT EXISTS FOR (n:Page) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT bibliography_type_name_unique IF NOT EXISTS FOR (n:BibliographyType) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT note_type_name_unique IF NOT EXISTS FOR (n:NoteType) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT attribute_type_name_unique IF NOT EXISTS FOR (n:AttributeType) REQUIRE n.name IS UNIQUE;
CREATE INDEX localized_text_text IF NOT EXISTS FOR (n:LocalizedText) ON (n.text);
