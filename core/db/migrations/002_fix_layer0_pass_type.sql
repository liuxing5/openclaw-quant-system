DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'funnel_results' 
        AND column_name = 'layer0_pass' 
        AND data_type = 'boolean'
    ) THEN
        ALTER TABLE funnel_results ALTER COLUMN layer0_pass TYPE INT USING CASE WHEN layer0_pass THEN 1 ELSE 0 END;
    END IF;
END $$;
