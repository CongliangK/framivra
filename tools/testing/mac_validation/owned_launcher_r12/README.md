# Owned validation launcher r12

Reviewed source delivery for an explicitly selected diagnostic resource census. Actual r12 Mac qualification is UNRUN; this source delivery does not authorize execution, merging or another comparison arm.

Read the [delivery and qualification guide](../../../../docs/mac-validation-tools.md) for checkout identity, portable bindings, exact selection, exclusions and remaining gates. The frozen [mode and schema contract](MODE-AND-SCHEMA.txt) is the observation contract home.

The run-spec field `resource_observation_mode` defaults to `strict_identity_v1`; diagnostic use requires the explicit value `diagnostic_census_v1`. An unknown mode fails. Source delivery does not supply a genuine run spec or input bundle.

[APPROVED-TEST-IDS.txt](APPROVED-TEST-IDS.txt) retains the original 68 selectors. [DIAGNOSTIC-TEST-IDS.txt](DIAGNOSTIC-TEST-IDS.txt) contains the six diagnostic selectors; [APPROVED-ALL-TEST-IDS.txt](APPROVED-ALL-TEST-IDS.txt) is their ordered 74-ID union. The separate exclusion lists remain unselected. Do not use discovery.

[The source manifest](../SOURCE-MANIFEST.json) distinguishes frozen executed source from the sole portable repository-binding adaptation. No generated diagnostic JSON, attempt evidence, runtime log or private path is included.
