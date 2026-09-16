CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS mediaforge_memory_project (
    namespace text NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    digest text NOT NULL,
    fingerprint text NOT NULL,
    PRIMARY KEY (namespace, tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS mediaforge_memory_chunk (
    memory_id bigserial PRIMARY KEY,
    namespace text NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    fingerprint text NOT NULL,
    kind text NOT NULL,
    content text NOT NULL,
    metadata jsonb NOT NULL,
    sha256 text NOT NULL,
    created_at double precision NOT NULL,
    embedding vector NOT NULL
);
CREATE INDEX IF NOT EXISTS mediaforge_memory_chunk_scope
    ON mediaforge_memory_chunk(namespace, tenant_id, project_id, fingerprint);

ALTER TABLE mediaforge_memory_project ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediaforge_memory_project FORCE ROW LEVEL SECURITY;
ALTER TABLE mediaforge_memory_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediaforge_memory_chunk FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mediaforge_memory_scope ON mediaforge_memory_project;
CREATE POLICY mediaforge_memory_scope ON mediaforge_memory_project
    USING (
        namespace = current_setting('mediaforge.memory_namespace', true)
        AND tenant_id = current_setting('mediaforge.memory_tenant', true)
        AND project_id IN (SELECT jsonb_array_elements_text(
            COALESCE(NULLIF(current_setting('mediaforge.memory_projects', true), ''), '[]')::jsonb))
    );
DROP POLICY IF EXISTS mediaforge_memory_scope ON mediaforge_memory_chunk;
CREATE POLICY mediaforge_memory_scope ON mediaforge_memory_chunk
    USING (
        namespace = current_setting('mediaforge.memory_namespace', true)
        AND tenant_id = current_setting('mediaforge.memory_tenant', true)
        AND project_id IN (SELECT jsonb_array_elements_text(
            COALESCE(NULLIF(current_setting('mediaforge.memory_projects', true), ''), '[]')::jsonb))
    );
