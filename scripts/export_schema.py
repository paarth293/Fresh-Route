"""Generate machine-readable JSON Schema for ScenarioPackage (Step 3)."""
import json
from pathlib import Path
from app.schemas.models import ScenarioPackage

ROOT = Path(__file__).resolve().parent.parent
schema = ScenarioPackage.model_json_schema()
out_path = ROOT / "docs" / "scenario_package.schema.json"
out_path.write_text(json.dumps(schema, indent=2))
print(f"Exported JSON Schema to {out_path}")
