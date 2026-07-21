import argparse
from pathlib import Path

from policyguard.application.data_evidence import (
    publish_evidence_bundle,
    validate_published_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish auditable, credential-free data evidence")
    parser.add_argument("--validate", action="store_true", help="validate the committed bundle")
    args = parser.parse_args()
    root = Path(__file__).parents[3]
    target = root / "data/evidence/v1"
    if args.validate:
        errors = validate_published_bundle(target)
        if errors:
            raise SystemExit("\n".join(errors))
        print("evidence bundle valid")
        return
    inventory = publish_evidence_bundle(root, target)
    print(
        f"published {inventory['counts']['latest_release_sources']} sources / "
        f"{inventory['counts']['latest_release_sections']} sections"
    )


if __name__ == "__main__":
    main()
