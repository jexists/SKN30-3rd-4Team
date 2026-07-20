create extension if not exists vector;

create table if not exists public.kb_experiments_v2 (
    experiment_id text primary key,
    manifest jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists public.kb_chunks_v2 (
    experiment_id text not null references public.kb_experiments_v2(experiment_id),
    chunk_id text not null,
    content text not null check (length(content) > 0),
    embedding vector(1024) not null,
    metadata jsonb not null,
    created_at timestamptz not null default now(),
    primary key (experiment_id, chunk_id)
);

create index if not exists kb_chunks_v2_embedding_hnsw_idx
on public.kb_chunks_v2 using hnsw (embedding vector_cosine_ops);

create or replace function public.match_kb_chunks_v2(
    query_embedding vector(1024),
    match_count integer,
    p_experiment_id text
)
returns table (
    chunk_id text,
    content text,
    metadata jsonb,
    similarity double precision
)
language sql stable
as $$
    select
        c.chunk_id,
        c.content,
        c.metadata,
        1 - (c.embedding <=> query_embedding) as similarity
    from public.kb_chunks_v2 c
    where c.experiment_id = p_experiment_id
    order by c.embedding <=> query_embedding
    limit match_count;
$$;
