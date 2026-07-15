# Drawing rule packs

- `presets/`: versioned JSON rule sets used by the drawing engine.
- `schema/`: configuration schema for editor validation and enterprise integration.
- `examples/`: project-specific override examples.

Set `PITGUARD_DRAWING_RULE_DIR` to another directory containing `presets/*.json` to load an enterprise rule package. Renderers remain server-side whitelist entries; JSON configuration cannot execute code.
