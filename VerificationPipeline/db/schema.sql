-- Single Postgres: corpus, source catalog, audit log, moderator cases.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- One row per catalog entry (conf/base/sources.yml); gov.pl has one entry per institution (path).
CREATE TABLE IF NOT EXISTS source (
    id               serial PRIMARY KEY,
    domain           text NOT NULL,
    path             text NOT NULL DEFAULT '',
    publisher        text NOT NULL,
    type             text NOT NULL CHECK (type IN ('primary', 'factcheck', 'secondary')),
    license          text,
    qualification_note text,
    update_policy    text,
    active           boolean NOT NULL DEFAULT true,
    UNIQUE (domain, path)
);

-- Every ingestion run that changes the index creates a version; checks record which one they used.
CREATE TABLE IF NOT EXISTS corpus_version (
    id               serial PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    embedding_model  text NOT NULL,
    dim              int  NOT NULL,
    n_docs           int  NOT NULL,
    n_chunks         int  NOT NULL,
    params           jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS document (
    id            text PRIMARY KEY,
    source_id     int NOT NULL REFERENCES source(id),
    url           text NOT NULL UNIQUE,
    title         text,
    published_at  timestamptz,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    content_hash  text NOT NULL,
    dup_group     text NOT NULL,
    seed_url      text,                 -- catalog start page it was reached from
    depth         int  NOT NULL DEFAULT 0,
    extract_version text NOT NULL DEFAULT 'trafilatura-2'
);

CREATE TABLE IF NOT EXISTS chunk (
    id           text PRIMARY KEY,
    document_id  text NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    position     int  NOT NULL DEFAULT 0,  -- order in the document (neighbour chunks)
    text         text NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    embedding    vector(1024),             -- bge-m3 dense dim
    corpus_version int REFERENCES corpus_version(id)
);
CREATE INDEX IF NOT EXISTS chunk_doc_pos_idx ON chunk (document_id, position);
CREATE INDEX IF NOT EXISTS chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunk_emb_idx ON chunk USING hnsw (embedding vector_cosine_ops);

-- Graph layer, stage 1 (deterministic, no LLM): entities and document relations.
-- Every edge points at evidence (chunk/doc) so it stays auditable. See docs/adr/0002-bazy-danych.md.
CREATE TABLE IF NOT EXISTS entity (
    id     serial PRIMARY KEY,
    name   text NOT NULL,
    type   text NOT NULL,              -- PERSON | ORG | GPE | LAW | ...
    UNIQUE (name, type)
);

CREATE TABLE IF NOT EXISTS mention (
    chunk_id   text NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    entity_id  int  NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, entity_id)
);
CREATE INDEX IF NOT EXISTS mention_entity_idx ON mention (entity_id);

CREATE TABLE IF NOT EXISTS document_relation (
    src_doc  text NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    dst_doc  text NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    type     text NOT NULL CHECK (type IN ('reprints', 'cites', 'corrects')),
    method   text NOT NULL,             -- minhash | link | manual | llm
    PRIMARY KEY (src_doc, dst_doc, type)
);

-- Graph layer, stage 2 (LLM, offline): argumentation between source fragments.
-- An edge is an interpretation, not a fact: quote must pass the citation validator.
CREATE TABLE IF NOT EXISTS claim_relation (
    src_chunk  text NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    dst_chunk  text NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    type       text NOT NULL CHECK (type IN ('supports', 'attacks', 'corrects')),
    quote      text NOT NULL,
    model      text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (src_chunk, dst_chunk, type)
);

CREATE TABLE IF NOT EXISTS "check" (
    id              text PRIMARY KEY,
    channel         text NOT NULL,
    query_masked    text NOT NULL,
    status          text NOT NULL,
    result_json     jsonb NOT NULL,
    trace_json      jsonb NOT NULL,
    injection_flag  boolean NOT NULL DEFAULT false,
    latency_ms      int,
    model           text,
    tokens          int,
    cost_usd        numeric(10, 6),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL DEFAULT now() + interval '30 days'
);

-- Recommendation != forwarding (task requirement).
CREATE TABLE IF NOT EXISTS "case" (
    id           serial PRIMARY KEY,
    cluster_key  text NOT NULL,
    check_ids    text[] NOT NULL,
    status       text NOT NULL CHECK (status IN ('recommended', 'forwarded')),
    created_at   timestamptz NOT NULL DEFAULT now()
);
