-- The MCP role reaches policy search only through these views; it has no access to base tables.
CREATE VIEW v_policy_search AS
  SELECT id, doc_id, text, embedder_id, embedding FROM policy_chunks;
CREATE VIEW v_embedder_state AS
  SELECT embedder_id, state FROM embedder_state;
