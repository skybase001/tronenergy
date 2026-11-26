ALTER TABLE chain_operations ADD COLUMN observed_at timestamptz;
CREATE INDEX operation_observation ON chain_operations(observed_at,created_at);
CREATE INDEX audit_target ON audit_events(target,event_id DESC);
