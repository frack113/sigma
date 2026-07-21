#!/usr/bin/env python3

"""Validate the info.yml files in regression_data/."""

import json
import os
import sys
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, Iterator, List, NoReturn, Tuple

import yaml
try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


# Known test types
KNOWN_TEST_TYPES = {"evtx"}


def load_schema(schema_dir: Path) -> Dict[str, Any]:
    """Load the JSON Schema for regression info files.

    Args:
        schema_dir (Path): Directory containing the schema file.

    Returns:
        Dict[str, Any]: The loaded JSON Schema, or None if not available.
    """
    schema_path = schema_dir / "sigma-detection-rule-regression-info-schema.json"
    if not schema_path.exists():
        warnings.warn(f"Schema file not found at {schema_path}, skipping schema validation")
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        warnings.warn(f"Failed to load schema file {schema_path}: {e}")
        return None


def validate_with_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Validate data against a JSON Schema.

    Args:
        data (Dict[str, Any]): Data to validate.
        schema (Dict[str, Any]): JSON Schema to validate against.

    Returns:
        List[str]: List of validation error messages.
    """
    if not HAS_JSONSCHEMA:
        return ["jsonschema library not available, skipping schema validation"]

    errors = []
    try:
        jsonschema.validate(data, schema)
    except jsonschema.exceptions.ValidationError as e:
        # Build a readable path from the error
        path = " -> ".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
        errors.append(f"[JSON Schema] {path}: {e.message}")
    except jsonschema.exceptions.SchemaError as e:
        errors.append(f"[JSON Schema] Invalid schema: {e.message}")
    return errors


def get_envs() -> Dict[str, Any]:
    """Normalize the environment variables used by the action and returns them as a dictionary.

    Returns:
        Dict[str, Any]: A dictionary containing the environment variables used by the action.
    """
    github_workspace = Path(os.environ.get("GITHUB_WORKSPACE", "./"))

    sigma_regression_path = os.environ.get("SIGMA_REGRESSION_PATH")

    if not sigma_regression_path:
        sigma_regression_path = [github_workspace / "regression_data"]
    else:
        sigma_regression_path = [
            github_workspace / Path(path.strip())
            for path in sigma_regression_path.splitlines(True)
            if path
        ]

    return {
        "GITHUB_WORKSPACE": github_workspace,
        "SIGMA_REGRESSION_PATH": sigma_regression_path,
    }


def generate_all_info_files(root: Path) -> Iterator[Path]:
    """Generate all info.yml files in the given root directory.

    Args:
        root (Path): Root directory to start the search.

    Yields:
        Iterator[Path]: Yields all info.yml files found.
    """
    for path in root.rglob("info.yml"):
        try:
            if not path.is_file():
                continue
        except PermissionError:
            warnings.warn(f"PermissionError: Could not access {path}, skipping file")
            continue
        yield path


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID (any version).

    Args:
        value (str): String to check.

    Returns:
        bool: True if valid UUID.
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def validate_info_file(file_path: Path, base_dir: Path) -> List[str]:
    """Validate a single info.yml file.

    Args:
        file_path (Path): Path to the info.yml file.
        base_dir (Path): Base directory for resolving relative paths.

    Returns:
        List[str]: List of error messages. Empty if valid.
    """
    errors = []

    # 1. Parse YAML
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"Invalid YAML: {e}"]

    if not isinstance(data, dict):
        return ["File does not contain a valid YAML mapping"]

    # 2. Check mandatory fields
    mandatory_fields = ["id", "description", "date", "author", "regression_tests_info"]
    for field in mandatory_fields:
        if field not in data:
            errors.append(f"Missing mandatory field '{field}'")

    # 3. Validate info.yml id (valid UUID)
    info_id = data.get("id")
    if info_id:
        if not isinstance(info_id, str) or not is_valid_uuid(info_id):
            errors.append(f"Field 'id' is not a valid UUID: {info_id}")
    else:
        errors.append("Field 'id' is missing or empty")

    # 4. Validate rule_metadata (optional but encouraged)
    rule_metadata = data.get("rule_metadata")
    if rule_metadata is not None:
        if not isinstance(rule_metadata, list):
            errors.append("Field 'rule_metadata' must be a list")
        else:
            for i, entry in enumerate(rule_metadata):
                if not isinstance(entry, dict):
                    errors.append(f"rule_metadata[{i}] is not a mapping")
                    continue
                meta_id = entry.get("id")
                if not meta_id:
                    errors.append(f"rule_metadata[{i}] is missing 'id'")
                elif not isinstance(meta_id, str) or not is_valid_uuid(meta_id):
                    errors.append(
                        f"rule_metadata[{i}].id is not a valid UUID: {meta_id}"
                    )
                title = entry.get("title")
                if not title:
                    errors.append(f"rule_metadata[{i}] is missing 'title'")

    # 5. Validate regression_tests_info
    regression_tests = data.get("regression_tests_info", [])
    if not isinstance(regression_tests, list):
        errors.append("Field 'regression_tests_info' must be a list")
    elif len(regression_tests) == 0:
        errors.append("Field 'regression_tests_info' must not be empty")
    else:
        for i, test in enumerate(regression_tests):
            if not isinstance(test, dict):
                errors.append(f"regression_tests_info[{i}] is not a mapping")
                continue

            # Check name
            name = test.get("name")
            if not name:
                errors.append(f"regression_tests_info[{i}] is missing 'name'")

            # Check type
            test_type = test.get("type")
            if not test_type:
                errors.append(f"regression_tests_info[{i}] is missing 'type'")
            elif test_type not in KNOWN_TEST_TYPES:
                errors.append(
                    f"regression_tests_info[{i}].type '{test_type}' is not known "
                    f"(known types: {', '.join(sorted(KNOWN_TEST_TYPES))})"
                )

            # Check provider
            provider = test.get("provider")
            if not provider:
                errors.append(f"regression_tests_info[{i}] is missing 'provider'")

            # Check match_count (optional, but must be valid if present)
            match_count = test.get("match_count")
            if match_count is not None:
                if not isinstance(match_count, int) or match_count < 0:
                    errors.append(
                        f"regression_tests_info[{i}].match_count must be "
                        f"a non-negative integer, got: {match_count}"
                    )

            # Check path exists
            test_path_str = test.get("path")
            if not test_path_str:
                errors.append(f"regression_tests_info[{i}] is missing 'path'")
            else:
                # Path is relative to the workspace root (base_dir is regression_data,
                # so resolve relative to base_dir's parent which is the workspace root)
                test_path = base_dir.parent / test_path_str
                resolved = test_path.resolve()
                if not resolved.exists():
                    errors.append(
                        f"regression_tests_info[{i}].path '{test_path_str}' "
                        f"resolves to '{resolved}' which does not exist"
                    )

                # Check associated JSON file exists (if EVTX exists)
                if test_type == "evtx" and resolved.exists():
                    json_path = resolved.with_suffix(".json")
                    if not json_path.exists():
                        warnings.warn(
                            f"Associated JSON file '{json_path.name}' not found "
                            f"for evtx file '{resolved.name}' in {file_path}"
                        )

    return errors


def validate_all(
    regression_paths: List[Path],
) -> Tuple[Dict[str, List[str]], int]:
    """Validate all info.yml files in the given paths.

    Args:
        regression_paths (List[Path]): List of paths to search for info.yml.

    Returns:
        Tuple[Dict[str, List[str]], int]: (errors_by_file, total_file_count)
    """
    errors_by_file: Dict[str, List[str]] = {}
    file_count = 0

    # Load JSON Schema from the first valid path's sibling directory
    schema = None
    for reg_path in regression_paths:
        if reg_path.exists():
            # Schema is in tests/validate-regression-info/
            schema_dir = Path(__file__).parent.resolve()
            schema = load_schema(schema_dir)
            if schema:
                break

    for reg_path in regression_paths:
        if not reg_path.exists():
            warnings.warn(f"Regression path {reg_path} does not exist")
            continue

        for info_file in generate_all_info_files(reg_path):
            file_count += 1
            errors = validate_info_file(info_file, reg_path)

            # Also validate against JSON Schema if available
            if schema:
                try:
                    with open(info_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        # Convert date objects to ISO strings for JSON Schema validation
                        data = json.loads(json.dumps(data, default=str))
                        schema_errors = validate_with_schema(data, schema)
                        errors.extend(schema_errors)
                except yaml.YAMLError:
                    pass  # Already caught by validate_info_file

            if errors:
                errors_by_file[str(info_file)] = errors

    return errors_by_file, file_count


def help() -> None:
    """Print a help message."""
    print("Validate info.yml files in regression_data/.")
    print()
    print("Usage: python validate.py")
    print()
    print("Environment variables:")
    print("  SIGMA_REGRESSION_PATH   Path(s) to regression_data directories")
    print("                          (one per line). Defaults to regression_data/")


def main():
    """Main entry point."""
    envs = get_envs()
    regression_paths = envs["SIGMA_REGRESSION_PATH"]

    print("=" * 60)
    print("REGRESSION INFO.YML VALIDATOR")
    print("=" * 60)
    print(f"Scanning paths: {regression_paths}")
    print()

    errors_by_file, file_count = validate_all(regression_paths)

    print(f"\nTotal info.yml files found: {file_count}")
    print(f"Files with errors: {len(errors_by_file)}")

    if errors_by_file:
        print()
        print("ERRORS:")
        print("-" * 60)
        for file_path, errors in sorted(errors_by_file.items()):
            print(f"\nFile: {file_path}")
            for error in errors:
                print(f"  ✗ {error}")
        print()
        print("=" * 60)
        print(f"FAILED: {len(errors_by_file)} file(s) with validation errors")
        print("=" * 60)
        sys.exit(1)
    else:
        print()
        print("=" * 60)
        print("SUCCESS: All info.yml files are valid!")
        print("=" * 60)


if __name__ == "__main__":
    main()