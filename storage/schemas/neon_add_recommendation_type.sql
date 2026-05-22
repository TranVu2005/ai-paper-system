ALTER TABLE IF EXISTS document_recommendations
ADD COLUMN IF NOT EXISTS recommendation_type VARCHAR(32);

